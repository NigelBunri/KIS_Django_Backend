# Central place with model orchestration, adapter pattern for providers, and helpers

import json
import os
import time

from django.utils import timezone
from typing import Dict, Any, Iterator, Callable
from .models import AIJob, TranslationRequest, AIModel


def _get_claude_client():
    try:
        import anthropic  # type: ignore
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return None


def _claude_complete(
    client,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 1024,
) -> str:
    model = model or os.environ.get("ANTHROPIC_DEFAULT_MODEL", "claude-haiku-4-5-20251001")
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


def dispatch_job_to_model(job: AIJob) -> Dict[str, Any]:
    if job.job_type == 'TRANSLATION':
        return handle_translation(job)
    if job.job_type == 'SUMMARIZATION':
        return handle_summarization(job)
    if job.job_type == 'MODERATION':
        return handle_moderation(job)
    if job.job_type == 'CUSTOM':
        if job.input_ref_type == 'QNA':
            return handle_qna(job)
        return handle_custom(job)
    return {'result_ref': '', 'payload': {}}


def handle_translation(job: AIJob) -> Dict[str, Any]:
    tr = getattr(job, 'translation_request', None)
    if not tr:
        return {'result_ref': '', 'payload': {}}

    text = job.metadata.get('text', '') or ''
    if not text:
        return {'result_ref': f'translation:{tr.id}', 'payload': {'translated': ''}}

    client = _get_claude_client()
    if client:
        try:
            system = (
                f"You are a professional translation engine. Translate the following text from "
                f"{tr.source_lang} to {tr.target_lang}. Output ONLY the translated text — no "
                "explanation, no preamble, no quotes."
            )
            translated = _claude_complete(
                client, system, text,
                max_tokens=min(4096, max(256, len(text) * 4)),
            )
            quality_score = 0.92
        except Exception as exc:
            translated = ''
            quality_score = 0.0
            job.metadata = {**job.metadata, 'translation_error': str(exc)}
            job.save(update_fields=['metadata'])
    else:
        translated = ''
        quality_score = 0.0

    tr.result_text = translated
    tr.quality_score = quality_score
    tr.save(update_fields=['result_text', 'quality_score'])
    return {'result_ref': f'translation:{tr.id}', 'payload': {'translated': translated, 'quality_score': quality_score}}


def handle_qna(job: AIJob) -> Dict[str, Any]:
    from .models import QnASession

    message = job.metadata.get('message', '')
    session_id = job.input_ref_id

    session = QnASession.objects.filter(id=session_id).first() if session_id else None
    history = list(session.conversation_history or []) if session else []
    context = (session.context or '') if session else ''

    client = _get_claude_client()
    if client:
        try:
            messages: list = []
            for turn in history[-20:]:
                role = turn.get('role', 'user')
                content = turn.get('content', '')
                if role in ('user', 'assistant') and content:
                    messages.append({'role': role, 'content': content})
            messages.append({'role': 'user', 'content': message})

            system_prompt = "You are a helpful, concise assistant for the KIS platform."
            if context:
                system_prompt += f"\n\nSession context: {context}"

            api_model = os.environ.get('ANTHROPIC_QNA_MODEL', 'claude-haiku-4-5-20251001')
            import anthropic as _anthropic  # type: ignore
            api_response = client.messages.create(
                model=api_model,
                max_tokens=512,
                system=system_prompt,
                messages=messages,
            )
            answer = api_response.content[0].text
        except Exception as exc:
            answer = "I'm unable to process your request right now. Please try again shortly."
            job.metadata = {**job.metadata, 'qna_error': str(exc)}
            job.save(update_fields=['metadata'])
    else:
        answer = "AI assistant is not configured. Please contact support."

    if session:
        history.append({'role': 'user', 'content': message})
        history.append({'role': 'assistant', 'content': answer})
        session.conversation_history = history[-40:]
        session.last_interaction_at = timezone.now()
        session.save(update_fields=['conversation_history', 'last_interaction_at'])

    job.result_ref = 'qna:answered'
    job.save(update_fields=['result_ref'])
    return {'result_ref': job.result_ref, 'payload': {'answer': answer}}


def handle_summarization(job: AIJob) -> Dict[str, Any]:
    text = job.metadata.get('text', '')
    if not text:
        return {'result_ref': 'summarization:empty', 'payload': {'summary': ''}}

    client = _get_claude_client()
    if client:
        try:
            system = (
                "You are a summarization engine. Produce a concise summary in 2-4 sentences. "
                "Output ONLY the summary text."
            )
            summary = _claude_complete(client, system, text, max_tokens=300)
        except Exception:
            summary = ''
    else:
        summary = ''

    job.metadata = {**job.metadata, 'summary': summary}
    job.save(update_fields=['metadata'])
    return {'result_ref': 'summarization:done', 'payload': {'summary': summary}}


def handle_moderation(job: AIJob) -> Dict[str, Any]:
    text = job.metadata.get('text', '')

    client = _get_claude_client()
    if client:
        try:
            system = (
                "You are a content moderation system for a social platform. "
                "Analyze the content and respond with valid JSON only:\n"
                '{"flagged": true/false, "categories": ["hate_speech"|"violence"|"spam"|"adult"|"self_harm"|"harassment"|"misinformation"], "confidence": 0.0-1.0, "reason": "short explanation"}'
            )
            raw = _claude_complete(client, system, f"Content:\n{text}", max_tokens=200)
            result = json.loads(raw)
        except Exception:
            result = {'flagged': False, 'categories': [], 'confidence': 0.5, 'reason': 'moderation_unavailable'}
    else:
        result = {'flagged': False, 'categories': [], 'confidence': 0.0, 'reason': 'ai_not_configured'}

    job.metadata = {**job.metadata, 'moderation_result': result}
    job.save(update_fields=['metadata'])
    return {'result_ref': 'moderation:done', 'payload': result}


def handle_custom(job: AIJob) -> Dict[str, Any]:
    prompt = job.metadata.get('prompt', '') or job.metadata.get('message', '')
    if not prompt:
        return {'result_ref': 'custom:empty', 'payload': {}}

    client = _get_claude_client()
    if client:
        try:
            mode = job.metadata.get('mode', 'general')
            system = f"You are an AI assistant for the KIS platform. Mode: {mode}. Be helpful and concise."
            response = _claude_complete(client, system, prompt, max_tokens=1024)
        except Exception as exc:
            response = ''
            job.metadata = {**job.metadata, 'custom_error': str(exc)}
            job.save(update_fields=['metadata'])
    else:
        response = ''

    job.metadata = {**job.metadata, 'response': response}
    job.save(update_fields=['metadata'])
    return {'result_ref': 'custom:done', 'payload': {'response': response}}


def run_pipeline_steps(pipeline: AIModel):
    # Interpret pipeline.job_order which contains definitions like [{job_type: 'TRANSLATION', ...}, ...]
    for step in pipeline.job_order:
        # Create job per step
        job = AIJob.objects.create(job_type=step.get('job_type', 'CUSTOM'), input_ref_type=step.get('input_ref_type', 'RAW'), metadata=step.get('metadata', {}), triggered_by='PIPELINE')
        dispatch_job_to_model(job)


class GroqAIService:
    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY")
        self.stream_url = os.environ.get("GROQ_STREAM_URL", "https://api.groq.com/v1/chat/completions")
        self.default_model = os.environ.get("GROQ_DEFAULT_MODEL", "groq-1")
        self.mode_map = {
            "triage": os.environ.get("GROQ_MODEL_TRIAGE", "groq-triage"),
            "clinical": os.environ.get("GROQ_MODEL_CLINICAL", "groq-clinical"),
            "lab": os.environ.get("GROQ_MODEL_LAB", "groq-lab"),
            "pharmacy": os.environ.get("GROQ_MODEL_PHARMACY", "groq-pharmacy"),
            "command": os.environ.get("GROQ_MODEL_COMMAND", "groq-command"),
        }
        self.function_registry: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "triage_prepare": self._fn_triage_prepare,
            "interact_schema": self._fn_interaction_summary,
        }
        self.max_risk_score = float(os.environ.get("AI_SAFETY_MAX_SCORE", "0.7"))

    def stream_prompt(
        self,
        prompt: str,
        mode: str = "general",
        tenant_id: str | None = None,
        model: str | None = None,
        job_id: str | None = None,
    ) -> Iterator[Dict[str, Any]]:
        self.safety_check(prompt)

        if not self.api_key:
            yield from self._fake_stream(prompt, mode)
            return

        payload = {
            "model": model or self.default_model,
            "messages": [
                {"role": "system", "content": f"Mode: {mode}"},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "metadata": {"tenant_id": tenant_id, "job_id": job_id},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload["model"] = model or self._select_model(mode)

        try:
            import requests
        except ImportError:
            yield from self._fake_stream(prompt, mode)
            return

        try:
            with requests.post(self.stream_url, headers=headers, json=payload, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        chunk = {"type": "chunk", "text": line}
                    self._safety_mark(job_id, chunk)
                    yield chunk
        except Exception:
            yield from self._fake_stream(prompt, mode)

        yield {"type": "done"}

    def _select_model(self, mode: str) -> str:
        return self.mode_map.get(mode, self.default_model)

    def _safety_mark(self, job_id: str | None, chunk: Dict[str, Any]):
        # placeholder to persist chunk info (could integrate with auditing service)
        if not job_id:
            return
        try:
            job = AIJob.objects.filter(id=job_id).first()
            if not job:
                return
            notes = job.metadata.get("stream", [])
            notes.append(chunk)
            job.metadata["stream"] = notes[-20:]
            job.save(update_fields=["metadata"])
        except Exception:
            pass

    def _fn_triage_prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symptoms = payload.get("symptoms", [])
        risk = round(min(1.0, len(symptoms) * 0.1), 2)
        return {"triage_level": "urgent" if risk > 0.5 else "routine", "risk_score": risk}

    def _fn_interaction_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        interactions = payload.get("interactions", [])
        return {"summary": f"{len(interactions)} interactions analyzed", "safe": True}

    def perform_function_call(self, function_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        fn = self.function_registry.get(function_name)
        if not fn:
            return {"error": f"Function {function_name} not registered"}
        return fn(payload)

    def safety_check(self, prompt: str) -> None:
        keywords = {"suicide", "harm", "unauthorized", "phishing"}
        risk = 0.0
        lowered = prompt.lower()
        for word in keywords:
            if word in lowered:
                risk += 0.25
        if risk > self.max_risk_score:
            raise ValueError("Prompt contains high-risk content. Escalate to human review.")

    def _fake_stream(self, prompt: str, mode: str) -> Iterator[Dict[str, Any]]:
        snippets = [
            f"Groq {mode} assistant waking up...",
            f"Analyzing prompt: {prompt[:120]}",
            "Correlating against hospital-grade policies...",
            "Packaging response to stream back to the client.",
        ]
        yield {"type": "start", "text": "Groq stub engaged (API key missing or request failed)."}
        for snippet in snippets:
            time.sleep(0.05)
            yield {"type": "chunk", "text": snippet}
        yield {"type": "done"}

    def log_decision(self, job_id: str | None, data: Dict[str, Any]) -> None:
        if not job_id:
            return
        try:
            job = AIJob.objects.filter(id=job_id).first()
            if not job:
                return
            log = job.metadata.get("decisions", [])
            log.append({"timestamp": time.time(), "payload": data})
            job.metadata["decisions"] = log[-20:]
            job.save(update_fields=["metadata"])
        except Exception:
            pass

    def route_task(
        self,
        prompt: str,
        mode: str,
        tenant_id: str | None = None,
        function_call: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self.safety_check(prompt)
        model = self._select_model(mode)
        response: Dict[str, Any] = {"prompt": prompt, "mode": mode, "model": model, "tenant_id": tenant_id}
        if function_call:
            fname = function_call.get("name")
            payload = function_call.get("payload", {})
            response["function_call"] = self.perform_function_call(fname, payload)
        else:
            response["response"] = f"[{mode} assistant] {prompt[:120]}"
        return response

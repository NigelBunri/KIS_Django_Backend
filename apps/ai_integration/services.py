# Central place with model orchestration, adapter pattern for providers, and helpers

import json
import os
import time

from typing import Dict, Any, Iterator, Callable
from .models import AIJob, TranslationRequest, AIModel


def dispatch_job_to_model(job: AIJob) -> Dict[str, Any]:
    """
    Decide which model / adapter to call based on job metadata and local availability.
    For a free tier, this uses a local lightweight model adapter (stub) or external free APIs.
    Returns dict with keys: result_ref, payload.
    """
    # Basic router by job_type
    if job.job_type == 'TRANSLATION':
        return handle_translation(job)
    if job.job_type == 'CUSTOM' and job.input_ref_type == 'QNA':
        return handle_qna(job)
    # other handlers...
    return {'result_ref': '', 'payload': {}}


def handle_translation(job: AIJob) -> Dict[str, Any]:
    # Fetch translation request
    tr = getattr(job, 'translation_request', None)
    if not tr:
        return {'result_ref': '', 'payload': {}}
    # Here you would call a real translation provider. For free tier implement a simple rule-based stub.
    # Example stub: reverse text as "translation" (replace with real model in paid tier)
    translated = tr.result_text or '<<translated_text_placeholder>>'
    # Save back
    tr.result_text = translated
    tr.quality_score = 0.0
    tr.save()
    return {'result_ref': f'translation:{tr.id}', 'payload': {'translated': translated}}


def handle_qna(job: AIJob) -> Dict[str, Any]:
    # Simple QnA stub: echo + context
    message = job.metadata.get('message', '')
    response = f"Echo (free stub): {message}"
    # Ideally append to session conversation
    # Save result_ref
    job.result_ref = 'qna:stub'
    job.save()
    return {'result_ref': job.result_ref, 'payload': {'answer': response}}


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

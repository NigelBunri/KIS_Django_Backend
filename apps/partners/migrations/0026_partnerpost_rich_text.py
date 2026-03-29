from django.db import migrations, models


def build_doc_from_text(text: str, styled: dict) -> dict:
    marks = []
    font_color = styled.get("fontColor")
    background_color = styled.get("backgroundColor")
    if font_color:
        marks.append({"type": "text_color", "attrs": {"color": font_color}})
    if background_color:
        marks.append({"type": "highlight", "attrs": {"color": background_color}})
    paragraph: dict = {"type": "paragraph", "content": []}
    if text:
        text_node: dict = {"type": "text", "text": text}
        if marks:
            text_node["marks"] = marks
        paragraph["content"].append(text_node)
    doc = {"type": "doc", "content": [paragraph]}
    return doc


def migrate_text(apps, schema_editor):
    PartnerPost = apps.get_model("partners", "PartnerPost")
    for post in PartnerPost.objects.all():
        raw_text = post.text or ""
        styled = getattr(post, "styled_text", {}) or {}
        text_value = styled.get("text") or raw_text or ""
        doc = build_doc_from_text(text_value, styled)
        post.text_doc = doc
        post.text_plain = text_value
        post.text_preview = text_value[:200]
        post.save(update_fields=["text_doc", "text_plain", "text_preview"])


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0025_merge_0022_partner_deactivation_0024_lesson_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerpost",
            name="text_doc",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Structured document payload for the post.",
            ),
        ),
        migrations.AddField(
            model_name="partnerpost",
            name="text_plain",
            field=models.TextField(
                blank=True,
                help_text="Plain text extracted from the rich content.",
            ),
        ),
        migrations.AddField(
            model_name="partnerpost",
            name="text_preview",
            field=models.CharField(
                blank=True,
                max_length=512,
                help_text="Short preview text to show in lists.",
            ),
        ),
        migrations.RunPython(migrate_text, migrations.RunPython.noop),
        migrations.RemoveField(model_name="partnerpost", name="styled_text"),
        migrations.RemoveField(model_name="partnerpost", name="text"),
        migrations.RenameField(model_name="partnerpost", old_name="text_doc", new_name="text"),
    ]

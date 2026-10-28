"""Generate portable static HTML run reports."""

import html
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from testbench.ai.redaction import redact_text

if TYPE_CHECKING:
    from testbench.core.runner import RunResult


def generate_report(result: "RunResult", reports_dir: Path = Path("reports")) -> Path:
    """Write a standalone report and copy linked screenshots/videos beside it."""
    report_dir = reports_dir / result.run_id
    assets_dir = report_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = Path(result.artifact_dir)
    asset_paths: dict[Path, str] = {}

    if artifact_dir.is_dir():
        for source in sorted(artifact_dir.rglob("*")):
            if source.is_file() and (
                source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webm", ".mp4", ".xml"}
                or source.name == "logcat.txt"
            ):
                relative = source.relative_to(artifact_dir)
                destination = assets_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                asset_paths[source.resolve()] = destination.relative_to(report_dir).as_posix()

    rows: list[str] = []
    generated_indexes = set(result.metadata.get("ai_features", {}).get("generated_steps", []))
    failure_index = next(
        (step.index for step in result.steps if step.status == "failed"),
        None,
    )
    for step in result.steps:
        screenshot = ""
        if step.screenshot:
            source_path = Path(step.screenshot).resolve()
            asset = asset_paths.get(source_path)
            if asset:
                escaped_asset = html.escape(asset, quote=True)
                screenshot = (
                    f'<a href="{escaped_asset}"><img class="shot" '
                    f'src="{escaped_asset}" alt="Step {step.index} screenshot"></a>'
                )
        error_text = step.error or ""
        if step.old_locator is not None:
            error_text += (
                f"\nSuggested ({step.healing_method}, {step.healing_confidence:.2f}): "
                f"{step.old_locator!r} → {step.new_locator!r}"
            )
        error = _safe_html_text(error_text)
        used_features = []
        if step.index in generated_indexes:
            used_features.append("AI generated")
        if step.status == "healed":
            used_features.append("Healed")
        if step.index == failure_index and result.metadata.get("ai_failure_explanation"):
            used_features.append("AI explained")
        feature_badges = "".join(f'<span class="ai-feature">{html.escape(label)}</span>' for label in used_features)
        rows.append(
            "<tr>"
            f"<td>{step.index}</td>"
            f"<td>{html.escape(step.action)}{feature_badges}</td>"
            f'<td><span class="status {html.escape(step.status)}">'
            f"{html.escape(step.status.upper())}</span></td>"
            f"<td>{step.duration_seconds:.3f}s</td>"
            f"<td>{error}</td>"
            f"<td>{screenshot}</td>"
            "</tr>"
        )

    video_links = []
    for source, asset in sorted(asset_paths.items(), key=lambda item: str(item[0])):
        if source.suffix.lower() in {".webm", ".mp4"}:
            escaped_asset = html.escape(asset, quote=True)
            video_links.append(
                f'<li><a href="{escaped_asset}">{html.escape(source.name)}</a>'
                f'<video controls preload="metadata" src="{escaped_asset}"></video></li>'
            )
    video_section = (
        '<section><h2>Recordings</h2><ul class="videos">' + "".join(video_links) + "</ul></section>"
        if video_links
        else ""
    )
    mobile_metadata = ""
    if result.target == "android":
        metadata = result.metadata
        mobile_items = [
            ("Device model", str(metadata.get("device_model", ""))),
            ("Android version", str(metadata.get("android_version", ""))),
            ("App package", str(metadata.get("app_package", ""))),
        ]
        mobile_rows = "".join(
            f"<li><strong>{html.escape(label)}:</strong> {html.escape(value or 'Unknown')}</li>"
            for label, value in mobile_items
        )
        logcat_asset = asset_paths.get((artifact_dir / "logcat.txt").resolve())
        source_assets = [
            asset
            for source, asset in sorted(asset_paths.items(), key=lambda item: str(item[0]))
            if source.suffix.lower() == ".xml"
        ]
        diagnostics = ""
        if logcat_asset:
            diagnostics += f'<li><a href="{html.escape(logcat_asset, quote=True)}">logcat.txt</a></li>'
        if source_assets:
            diagnostics += "".join(
                f'<li><a href="{html.escape(asset, quote=True)}">'
                f"{'Failure page source' if Path(asset).name.startswith('failure_step_') else 'Page source'} "
                f"{html.escape(Path(asset).name)}</a></li>"
                for asset in source_assets
            )
        mobile_items_html = (
            f"<section><h2>Android details</h2><ul>{mobile_rows}</ul>"
            f"{'<h3>Diagnostics</h3><ul>' + diagnostics + '</ul>' if diagnostics else ''}"
            "</section>"
        )
        mobile_metadata = mobile_items_html
    game_metadata = ""
    if result.target == "game":
        metadata = result.metadata
        surface = html.escape(str(metadata.get("surface", "Unknown")))
        resolution = html.escape(str(metadata.get("resolution", "Unknown")))
        match_rows = []
        for match in metadata.get("matches", []):
            image_name = html.escape(str(match.get("image", "image")))
            confidence = match.get("confidence")
            confidence_text = f"{float(confidence):.3f}" if confidence is not None else "AI fallback"
            annotation = ""
            annotation_path = match.get("annotated")
            if annotation_path:
                asset = asset_paths.get(Path(annotation_path).resolve())
                if asset:
                    safe_asset = html.escape(asset, quote=True)
                    annotation = (
                        f'<a href="{safe_asset}"><img class="shot" '
                        f'src="{safe_asset}" alt="Annotated match for {image_name}"></a>'
                    )
            match_rows.append(f"<tr><td>{image_name}</td><td>{confidence_text}</td><td>{annotation}</td></tr>")
        matches_section = (
            "<h3>Image matches</h3><table><thead><tr><th>Template</th>"
            "<th>Confidence</th><th>Annotated screenshot</th></tr></thead><tbody>"
            + "".join(match_rows)
            + "</tbody></table>"
            if match_rows
            else ""
        )
        fps_rows = []
        for item in metadata.get("fps", []):
            fps_rows.append(
                "<tr>"
                f"<td>{float(item.get('seconds', 0)):.2f}s</td>"
                f"<td>{float(item.get('fps', 0)):.2f}</td>"
                f"<td>{float(item.get('min_fps', 0)):.2f}</td>"
                f"<td>{'PASS' if item.get('passed') else 'FAIL'}</td>"
                "</tr>"
            )
        fps_section = (
            "<h3>Frame-rate measurements</h3><table><thead><tr>"
            "<th>Duration</th><th>Measured FPS</th><th>Minimum FPS</th><th>Status</th>"
            "</tr></thead><tbody>" + "".join(fps_rows) + "</tbody></table>"
            if fps_rows
            else ""
        )
        fallback_uses = metadata.get("ai_fallback_uses", [])
        if fallback_uses:
            fallback_note = f"<p><strong>AI vision fallback:</strong> used {len(fallback_uses)} time(s).</p>"
        else:
            fallback_status = metadata.get("ai_fallback_status")
            suffix = f" ({html.escape(str(fallback_status))})" if fallback_status else ""
            fallback_note = f"<p><strong>AI vision fallback:</strong> not used{suffix}.</p>"
        text_click_rows = []
        for click in metadata.get("text_clicks", []):
            image = ""
            annotation_path = click.get("annotated")
            if annotation_path:
                asset = asset_paths.get(Path(annotation_path).resolve())
                if asset:
                    safe_asset = html.escape(asset, quote=True)
                    image = (
                        f'<a href="{safe_asset}"><img class="shot" src="{safe_asset}" alt="Text click location"></a>'
                    )
            text_click_rows.append(f"<tr><td>{html.escape(str(click.get('text', '')))}</td><td>{image}</td></tr>")
        text_click_section = (
            "<h3>Text clicks</h3><table><thead><tr><th>Text</th>"
            "<th>Annotated screenshot</th></tr></thead><tbody>" + "".join(text_click_rows) + "</tbody></table>"
            if text_click_rows
            else ""
        )
        failure_assets = []
        for item in metadata.get("match_failures", []):
            annotated = asset_paths.get(Path(item["annotated"]).resolve())
            template = asset_paths.get(Path(item["template"]).resolve())
            links = []
            if annotated:
                links.append(f'<a href="{html.escape(annotated, quote=True)}">annotated candidate</a>')
            if template:
                links.append(f'<a href="{html.escape(template, quote=True)}">template image</a>')
            if links:
                failure_assets.append(
                    f"<li>{html.escape(str(item.get('image', 'image')))}: " + " · ".join(links) + "</li>"
                )
        failure_section = (
            "<h3>Failed match diagnostics</h3><ul>" + "".join(failure_assets) + "</ul>" if failure_assets else ""
        )
        game_metadata = (
            "<section><h2>Game details</h2>"
            f"<p><strong>Surface:</strong> {surface} · "
            f"<strong>Resolution:</strong> {resolution}</p>"
            f"{matches_section}{fps_section}{text_click_section}"
            f"{fallback_note}{failure_section}"
            "</section>"
        )
    passed = sum(step.status == "passed" for step in result.steps)
    failed = sum(step.status == "failed" for step in result.steps)
    skipped = sum(step.status == "skipped" for step in result.steps)
    healed = sum(step.status == "healed" for step in result.steps)
    status_class = html.escape(result.status)
    failure_explanation = ""
    if result.metadata.get("failure_explanation"):
        detail = result.metadata["failure_explanation"]
        fixes = "".join(f"<li>{_safe_html_text(item)}</li>" for item in detail.get("suggested_fixes", []))
        explanation_source = html.escape(str(detail.get("source", "rule-based")))
        failure_explanation = (
            "<section><h2>Failure explanation</h2>"
            f"<p><strong>{_safe_html_text(detail.get('category', 'unknown'))}</strong> · {explanation_source}</p>"
            f"<p>{_safe_html_text(detail.get('explanation', ''))}</p><ul>{fixes}</ul></section>"
        )
    ai_explanation = result.metadata.get("ai_failure_explanation")
    if ai_explanation:
        fixes = "".join(f"<li>{_safe_html_text(item)}</li>" for item in ai_explanation.get("suggested_fixes", []))
        failure_explanation += (
            "<section><h2>AI suggestion, may be wrong</h2>"
            f"<p><strong>{_safe_html_text(ai_explanation.get('category', 'unknown'))}</strong></p>"
            f"<p>{_safe_html_text(ai_explanation.get('explanation', ''))}</p>"
            f"<ul>{fixes}</ul></section>"
        )
    healing_section = ""
    suggestions = result.metadata.get("healing_suggestions", [])
    if suggestions:
        healing_rows = "".join(
            "<tr>"
            f"<td>{item.get('step_index', '')}</td>"
            f"<td><code>{_safe_html_text(item.get('old_locator'))}</code></td>"
            f"<td><code>{_safe_html_text(item.get('new_locator'))}</code></td>"
            f"<td>{html.escape(str(item.get('method', '')))}</td>"
            f"<td>{float(item.get('confidence', 0)):.2f}</td>"
            f"<td>{_healing_apply_button(item.get('id'))}</td>"
            "</tr>"
            for item in suggestions
        )
        healing_section = (
            "<section><h2>Locator healing suggestions</h2>"
            "<p>Review before applying; suggestions may be incorrect.</p>"
            "<table><thead><tr><th>Step</th><th>Old locator</th><th>Suggested locator</th>"
            "<th>Method</th><th>Confidence</th><th>Action</th></tr></thead><tbody>"
            + healing_rows
            + '</tbody></table><p class="apply-status" role="status"></p></section>'
        )
    ai_usage = result.metadata.get("ai_usage")
    ai_usage_section = ""
    ai_features = result.metadata.get("ai_features", {})
    feature_items = []
    generated_steps = ai_features.get("generated_steps", [])
    if generated_steps:
        feature_items.append("Generated steps: " + ", ".join(str(item) for item in generated_steps))
    if result.metadata.get("healed_steps"):
        feature_items.append(
            "Healed steps: " + ", ".join(str(item.get("step_index", "")) for item in result.metadata["healed_steps"])
        )
    if result.metadata.get("ai_failure_explanation"):
        feature_items.append("Failure explanation generated.")
    if feature_items:
        ai_usage_section += (
            "<section><h2>AI features used</h2><ul>"
            + "".join(f"<li>{html.escape(item)}</li>" for item in feature_items)
            + "</ul></section>"
        )
    if ai_usage:
        ai_usage_section += (
            "<section><h2>AI usage</h2><p>"
            f"Provider: {html.escape(str(result.metadata.get('ai_provider', 'unknown')))} · "
            f"Model: {html.escape(str(result.metadata.get('ai_model', 'unknown')))} · "
            f"Calls: {int(ai_usage.get('calls', 0))} · "
            f"Estimated tokens: {int(ai_usage.get('estimated_tokens', 0))} · "
            f"Cache hits: {int(ai_usage.get('cache_hits', 0))}</p></section>"
        )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(result.test_name)} — TestBench report</title>
  <style>
    :root {{ color-scheme: light dark; font: 16px/1.5 system-ui, sans-serif; }}
    body {{ max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ margin-bottom: .25rem; }}
    .summary, .status {{ font-weight: 700; }}
    .passed {{ color: #15803d; }} .failed {{ color: #dc2626; }}
    .flaky {{ color: #a16207; background: #fef3c7; padding: .1rem .4rem; border-radius: .3rem; }}
    .skipped {{ color: #64748b; }} .healed {{ color: #a16207; background: #fef3c7; }}
    .running {{ color: #a16207; }} table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #8886; padding: .65rem; text-align: left; vertical-align: top; }}
    .shot {{ display: block; max-width: 220px; max-height: 150px; object-fit: contain; }}
    .ai-feature {{ display: inline-block; margin: .25rem; padding: .1rem .4rem; border-radius: .4rem; background: #dbeafe; color: #1e3a8a; font-size: .75rem; }}
    .videos {{ list-style: none; padding: 0; }}
    .videos li {{ margin: 1rem 0; }} video {{ display: block; max-width: 100%; margin-top: .5rem; }}
  </style>
</head>
<body>
  <h1>{html.escape(result.test_name)}</h1>
  <p class="summary"><span class="{status_class}">{status_class.upper()}</span>
     {"<span class='flaky'>FLAKY ON RETRY</span>" if result.metadata.get("flaky") else ""}
     · {html.escape(result.target)} · {result.duration_seconds:.2f}s</p>
  <p>Run ID: <code>{html.escape(result.run_id)}</code> ·
     {passed} passed · {failed} failed · {skipped} skipped · {healed} healed</p>
  <table>
    <thead><tr><th>#</th><th>Action</th><th>Status</th><th>Duration</th>
      <th>Error</th><th>Screenshot</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  {healing_section}
  {failure_explanation}
  {ai_usage_section}
  {video_section}
  {mobile_metadata}
  {game_metadata}
  <script>
    for (const button of document.querySelectorAll("[data-healing-id]")) {{
      button.addEventListener("click", async () => {{
        const status = document.querySelector(".apply-status");
        button.disabled = true;
        try {{
          const id = button.dataset.healingId;
          const headers = {{"Content-Type": "application/json"}};
          const decision = await fetch(`/api/ai/healing/${{id}}/decision`, {{
            method: "POST", headers, body: JSON.stringify({{accepted: true}})
          }});
          if (!decision.ok) throw new Error("Could not accept this suggestion.");
          const applied = await fetch(`/api/ai/healing/${{id}}/apply`, {{
            method: "POST", headers, body: JSON.stringify({{}})
          }});
          const result = await applied.json();
          if (!applied.ok) throw new Error(result.detail || "Could not apply the fix.");
          status.textContent = `Applied step ${{result.step_index}} to ${{result.path}}.`;
        }} catch (error) {{
          status.textContent = error.message;
          button.disabled = false;
        }}
      }});
    }}
  </script>
</body>
</html>
"""
    report_path = report_dir / "index.html"
    report_path.write_text(document, encoding="utf-8")
    return report_path


def _healing_apply_button(record_id: object) -> str:
    """Render a one-click apply action only for a valid stored history ID."""
    if not isinstance(record_id, int) or record_id <= 0:
        return "Unavailable"
    return f'<button type="button" data-healing-id="{record_id}">Apply fix</button>'


def _safe_html_text(value: object) -> str:
    """Escape and redact report text before rendering locally stored values."""
    return html.escape(redact_text(str(value))[0])

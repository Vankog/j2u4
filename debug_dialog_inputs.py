"""One-shot inspector: open Add+Zoom dialog and dump all editable inputs.

Run once when the dialog form structure is unclear. The output gives us
ground-truth IDs/selectors for ArbAuft, Ticketno, Text fields — without
which we are guessing.

Does NOT save anything. Browser stays open after the dump for visual
verification.
"""
import asyncio
from utils import load_config_safe
from unit4_browser import Unit4Browser


async def main():
    config = load_config_safe()
    if not config:
        return

    async with Unit4Browser(config) as unit4:
        frame = await unit4.navigate_to_zeiterfassung()
        await unit4.set_week("202615")
        await unit4.wait_for_ready()

        print("[*] Click Add...")
        await unit4._click_by_id(frame, "button[id$='_newButton']")
        await asyncio.sleep(1)

        print("[*] Click Zoom (last button to target new row)...")
        zoom = unit4.frame_manager.page  # we'll resolve below
        try:
            zoom_btn = frame.locator("button[id$='_zoom']").last
            await zoom_btn.click(timeout=5000)
        except Exception as e:
            print(f"[!] Zoom failed: {e}")
            return
        await asyncio.sleep(2)

        # Dump all visible editable inputs/textareas across all frames
        js = """
        () => {
            const out = [];
            const sel = "input, textarea";
            for (const el of document.querySelectorAll(sel)) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                if (el.type === 'hidden') continue;
                const isDisabled = el.disabled || el.classList.contains('aspNetDisabled');
                out.push({
                    tag: el.tagName,
                    id: el.id || '',
                    name: el.name || '',
                    type: el.type || '',
                    value: (el.value || '').slice(0, 30),
                    cls: (el.className || '').slice(0, 80),
                    disabled: isDisabled,
                    title: el.title || '',
                    role: el.getAttribute('role') || '',
                    placeholder: el.placeholder || '',
                    parentTag: el.parentElement ? el.parentElement.tagName : '',
                    parentId: el.parentElement ? el.parentElement.id || '' : '',
                });
            }
            return out;
        }
        """
        for f in unit4.page.frames:
            try:
                results = await f.evaluate(js)
            except Exception:
                continue
            if not results:
                continue
            print(f"\n=== Frame: {f.url[:80]}")
            for r in results:
                marker = "DISABLED" if r['disabled'] else "ENABLED "
                print(f"  [{marker}] <{r['tag']}> id='{r['id']}' name='{r['name']}' type='{r['type']}'")
                print(f"             title='{r['title']}' role='{r['role']}' value='{r['value']}'")
                print(f"             cls='{r['cls']}'")
                print(f"             parent=<{r['parentTag']} id='{r['parentId']}'>")

        print("\n[*] Inspektion fertig. Browser bleibt offen — kein Save!")
        print("[*] Drück ENTER zum Schliessen (oder Ctrl+C)...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except (EOFError, KeyboardInterrupt):
            pass


asyncio.run(main())

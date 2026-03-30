"""Auto mode API Playwright test"""
import asyncio
import json
import sys

# Windows stdout encoding fix
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

BASE = "http://localhost:8000"
SS = "screenshots"
CWD = "C:/dev/github/Ghost-Developer"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 0. Cleanup - stop any running auto mode from previous test
        await page.request.post(f"{BASE}/auto/stop")
        await page.wait_for_timeout(300)

        # 1. Main UI screenshot
        print("[1] Main UI")
        await page.goto(BASE)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{SS}/01_main.png")
        print("    -> screenshots/01_main.png saved")

        # 2. GET /auto/status - initial
        print("[2] GET /auto/status - initial")
        resp = await page.request.get(f"{BASE}/auto/status")
        status = await resp.json()
        print(f"    -> {status}")
        assert status["is_running"] is False, f"Expected False, got {status['is_running']}"
        print("    OK: is_running=False")

        # 3. POST /auto/start
        print("[3] POST /auto/start")
        resp = await page.request.post(
            f"{BASE}/auto/start",
            data=json.dumps({"cwd": CWD, "interval_seconds": 9999}),
            headers={"Content-Type": "application/json"},
        )
        body = await resp.json()
        print(f"    -> {body}")
        assert "chat_id" in body, f"No chat_id in: {body}"
        assert body["status"] == "started"
        chat_id = body["chat_id"]
        print(f"    OK: started, chat_id={chat_id}")

        # 4. GET /auto/status - running
        print("[4] GET /auto/status - running")
        await page.wait_for_timeout(500)
        resp = await page.request.get(f"{BASE}/auto/status")
        status = await resp.json()
        print(f"    -> {status}")
        assert status["is_running"] is True
        assert status["cwd"] == CWD
        print("    OK: is_running=True")

        # 5. Duplicate start -> error
        print("[5] POST /auto/start duplicate")
        resp = await page.request.post(
            f"{BASE}/auto/start",
            data=json.dumps({"cwd": CWD}),
            headers={"Content-Type": "application/json"},
        )
        dup = await resp.json()
        print(f"    -> status={resp.status}, body={dup}")
        assert resp.status == 400
        assert "error" in dup
        print("    OK: duplicate rejected with 400")

        # 6. Screenshot while running
        await page.screenshot(path=f"{SS}/06_running.png")
        print("[6] -> screenshots/06_running.png saved")

        # 7. POST /auto/stop
        print("[7] POST /auto/stop")
        resp = await page.request.post(f"{BASE}/auto/stop")
        body = await resp.json()
        print(f"    -> {body}")
        assert body["status"] == "stopped"
        print("    OK: stopped")

        # 8. GET /auto/status - stopped
        print("[8] GET /auto/status - stopped")
        await page.wait_for_timeout(300)
        resp = await page.request.get(f"{BASE}/auto/status")
        status = await resp.json()
        print(f"    -> {status}")
        assert status["is_running"] is False
        print("    OK: is_running=False")

        # 9. Final screenshot
        await page.goto(BASE)
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{SS}/09_final.png")
        print("[9] -> screenshots/09_final.png saved")

        await browser.close()
        print("\nAll tests passed")


asyncio.run(main())

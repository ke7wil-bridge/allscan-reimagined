#!/usr/bin/env python3
"""Complete the official first-user form on the local QA VM only."""
import json
import secrets
import subprocess

from playwright.sync_api import sync_playwright
from asr_vm import runtime_root, ssh_args, WEB_PORT

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel='msedge', headless=True)
    try:
        page = browser.new_page()
        page.goto(f'http://127.0.0.1:{WEB_PORT}/allscan/')
        if 'No users have yet been created.' not in page.locator('body').inner_text():
            raise RuntimeError('First-run setup is not available; existing users preserved')
        password = secrets.token_urlsafe(12)
        credentials = {'email': 'asrqa@example.invalid', 'password': password}
        # Keep the new test-only login in the guest, never in Git or console output.
        save = "sudo python3 -c 'import sys,pathlib; p=pathlib.Path(\"/root/asr-qa/web-admin.json\"); p.write_text(sys.stdin.read()); p.chmod(0o600)'"
        subprocess.run(ssh_args(runtime_root()) + [save], input=json.dumps(credentials),
                       text=True, capture_output=True, check=True)
        for name, value in {'name': 'Thomas QA', 'email': credentials['email'],
                            'pass': password, 'location': 'Private offline QA',
                            'nodenums': '1999'}.items():
            page.locator(f'input[name="{name}"]').fill(value)
        page.locator('input[type="submit"]').click()
        page.wait_for_load_state('domcontentloaded')
        assert 'No users have yet been created.' not in page.locator('body').inner_text()
        print('Created fresh local QA admin; credentials retained only in guest root directory')
    finally:
        browser.close()

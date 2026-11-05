import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { browserRequest, permittedBilibiliURL } from '../core/browser-bridge.js';

test('browser bridge allows only bounded Bilibili read URLs', () => {
  assert.equal(permittedBilibiliURL('https://api.bilibili.com/x/web-interface/view?bvid=BV123'), true);
  assert.equal(permittedBilibiliURL('https://i0.hdslb.com/image.png'), true);
  assert.equal(permittedBilibiliURL('https://b23.tv/abcd'), true);
  for (const url of ['http://api.bilibili.com/x/web-interface/view', 'https://api.bilibili.com/x/space/myinfo',
    'https://passport.bilibili.com/', 'https://api.bilibili.com.evil.example/x/web-interface/view',
    'https://user:password@api.bilibili.com/x/web-interface/view', 'file:///etc/passwd']) {
    assert.equal(permittedBilibiliURL(url), false, url);
  }
});

test('browser credentials stay in session and unsigned requests do not reach network', async () => {
  let calls = 0, signedIn = false;
  const browser = {
    cookies: { async get() { return signedIn ? [{ value: 'do-not-return-this-secret' }] : []; } },
    async fetch(_url: string, init: RequestInit) {
      calls++; assert.equal(init.credentials, 'include'); assert.equal(init.redirect, 'manual');
      return new Response('test', { status: 200 });
    },
  };
  assert.deepEqual(await browserRequest(browser, { op: 'status' }), { logged_in: false });
  assert.deepEqual(await browserRequest(browser, { op: 'get', url: 'https://api.bilibili.com/x/web-interface/view' }), { error: 'login_required' });
  assert.equal(calls, 0);
  signedIn = true;
  const result = await browserRequest(browser, { op: 'get', url: 'https://api.bilibili.com/x/web-interface/view' });
  assert.equal(result.status, 200); assert.equal(Buffer.from(String(result.body), 'base64').toString(), 'test');
  assert.equal(JSON.stringify(result).includes('do-not-return-this-secret'), false);
  assert.equal(calls, 1);
});

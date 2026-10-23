import {test, expect} from '../fixtures';

test('phase 0: token opens the built app on the runtime origin', async ({app, runtime}) => {
  await expect(app.getByRole('heading', {name: 'Your next audiobook starts with a book.', exact: true})).toBeVisible();
  expect(new URL(app.url()).origin).toBe(runtime.origin);
  expect(new URL(app.url()).hash).toBe('');
  await expect(app.locator('script[type="module"][src^="/assets/"]')).toHaveCount(1);
  expect(await runtime.api('library', {})).toEqual([]);
  expect((await runtime.api('settings', {})).language).toBe('en');
  expect((await runtime.api('health', {})).ready).toBe(true);
});

test('phase 0: missing token surfaces the connection error', async ({page, runtime, request}) => {
  await page.goto(runtime.origin);
  await expect(page.getByText('Start the local runtime and open the URL printed in its terminal. No cloud service is used.', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Retry', exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Open book', exact: true})).toHaveCount(0);
  const response = await request.post(`${runtime.origin}/api`, {data: {op: 'library', data: {}}});
  expect(response.ok()).toBe(false);
  expect(await runtime.api('library', {})).toEqual([]);
});

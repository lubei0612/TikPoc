import { expect, test, type Page } from "@playwright/test";

function rejectConsoleErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  return errors;
}

async function expectNoViewportOverflow(page: Page) {
  const overflow = await page.locator("body").evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    return {
      body: document.body.scrollWidth,
      viewport,
      offenders: Array.from(document.querySelectorAll<HTMLElement>("body *"))
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.right > viewport + 1 && getComputedStyle(element).position !== "fixed";
        })
        .slice(0, 12)
        .map((element) => `${element.tagName}.${element.className}:${Math.round(element.getBoundingClientRect().right)}`),
    };
  });
  if (overflow.body > overflow.viewport + 1) {
    throw new Error(`Viewport overflow ${overflow.body}/${overflow.viewport}\n${overflow.offenders.join("\n")}`);
  }
}

async function expectControlsDoNotOverlap(page: Page) {
  const overlaps = await page.locator("body").evaluate(() => {
    const overlayLayer = (element: HTMLElement) => {
      let current: HTMLElement | null = element;
      while (current && current !== document.body) {
        const position = getComputedStyle(current).position;
        if (position === "fixed" || position === "sticky") return current;
        current = current.parentElement;
      }
      return document.body;
    };
    const controls = Array.from(document.querySelectorAll<HTMLElement>("button, input, select, textarea"))
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        const x = Math.min(innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
        const y = Math.min(innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
        const hit = document.elementFromPoint(x, y);
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0
          && rect.bottom > 0 && rect.top < innerHeight && Boolean(hit && (element === hit || element.contains(hit)));
      });
    return controls.flatMap((left, index) => controls.slice(index + 1).flatMap((right) => {
      if (left.contains(right) || right.contains(left)) return [];
      if (overlayLayer(left) !== overlayLayer(right)) return [];
      const a = left.getBoundingClientRect();
      const b = right.getBoundingClientRect();
      const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      return width > 1 && height > 1
        ? [`${left.ariaLabel || left.textContent} / ${right.ariaLabel || right.textContent}`]
        : [];
    }));
  });
  expect(overlaps).toEqual([]);
}

async function expectPageControlsDoNotOverlap(page: Page) {
  for (const fraction of [0, 0.5, 1]) {
    await page.evaluate((value) => window.scrollTo(0, (document.documentElement.scrollHeight - innerHeight) * value), fraction);
    await expectControlsDoNotOverlap(page);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

test("operations shows mobile traces and sticky horizontal coverage", async ({ page }, testInfo) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/operations");
  await expect(page.getByRole("heading", { name: "Device runtime" })).toBeVisible();
  await expect(page.getByTestId("coverage-matrix").getByText("long_target_identity_for_mobile_layout_verification")).toBeVisible();
  await expect(page.getByRole("button", { name: /Retry phone-03/ })).toBeVisible();
  await page.getByRole("button", { name: "Stop round" }).click();
  const dialog = page.getByRole("dialog", { name: "Confirm stop round" });
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox).not.toBeNull();
  expect(dialogBox!.x).toBeGreaterThanOrEqual(0);
  expect(dialogBox!.y).toBeGreaterThanOrEqual(0);
  expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  expect(dialogBox!.y + dialogBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Stop phone-01-long-identity" }).click();
  await expect(page.getByRole("dialog", { name: "Confirm stop phone-01-long-identity" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();

  const scroller = page.getByTestId("coverage-scroller");
  await expect(scroller).toBeVisible();
  const scrollable = await scroller.evaluate((element) => element.scrollWidth > element.clientWidth);
  if (testInfo.project.name === "mobile") expect(scrollable).toBe(true);
  if (scrollable) {
    await scroller.scrollIntoViewIfNeeded();
    await scroller.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
    const [containerBox, targetBox] = await Promise.all([
      scroller.boundingBox(),
      page.getByTestId("coverage-target-header").boundingBox(),
    ]);
    expect(containerBox).not.toBeNull();
    expect(targetBox).not.toBeNull();
    expect(targetBox!.x).toBeGreaterThanOrEqual(containerBox!.x - 1);
    expect(targetBox!.x + targetBox!.width).toBeLessThanOrEqual(containerBox!.x + containerBox!.width + 1);
  }
  await expectNoViewportOverflow(page);
  await expectPageControlsDoNotOverlap(page);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("operations.png"), fullPage: true });
});

test("inbox exposes readiness and operator controls in a bounded drawer", async ({ page }, testInfo) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Lead conversion" })).toBeVisible();
  await page.getByRole("button", { name: /Open buyer_with_a_very_long/ }).click();
  await expect(page.getByText("Private channel configured")).toBeVisible();
  await expect(page.getByRole("button", { name: /Take over/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create send plan" })).toBeVisible();
  await expectNoViewportOverflow(page);
  await expectPageControlsDoNotOverlap(page);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("inbox.png"), fullPage: true });
});

test("analytics separates measured evidence from projection", async ({ page }, testInfo) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { name: "Acquisition economics" })).toBeVisible();
  await expect(page.getByText("Measured completions")).toBeVisible();
  await expect(page.getByText("Projected daily capacity")).toBeVisible();
  await expectNoViewportOverflow(page);
  await expectPageControlsDoNotOverlap(page);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("analytics.png"), fullPage: true });
});

test("navigation preserves direct routes and browser history", async ({ page }) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/operations");
  await page.getByRole("button", { name: "Inbox" }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "Lead conversion" })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/operations$/);
  await expect(page.getByRole("heading", { name: "Device runtime" })).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "Lead conversion" })).toBeVisible();
  await page.getByRole("button", { name: "Analytics" }).click();
  await expect(page).toHaveURL(/\/analytics$/);
  expect(errors).toEqual([]);
});

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

async function expectOperationsBandsDoNotOverlap(page: Page) {
  const overlapReport = await page.locator(".operations-workspace").evaluate((workspace) => {
    const overlap = (top: DOMRect, bottom: DOMRect) => Math.min(top.bottom, bottom.bottom) - Math.max(top.top, bottom.top);
    const tables = Array.from(workspace.querySelectorAll<HTMLTableElement>("table"));
    const tableOverlaps = tables.flatMap((table) => {
      const header = table.tHead?.getBoundingClientRect();
      const firstRow = table.tBodies[0]?.rows[0]?.getBoundingClientRect();
      return header && firstRow && overlap(header, firstRow) > 1
        ? [`${table.className}:表头与首行重叠 ${Math.round(overlap(header, firstRow))}px`]
        : [];
    });
    const sections = Array.from(workspace.querySelectorAll<HTMLElement>(":scope > .workspace-section"));
    const sectionOverlaps = sections.slice(0, -1).flatMap((section, index) => {
      const current = section.getBoundingClientRect();
      const next = sections[index + 1].getBoundingClientRect();
      return overlap(current, next) > 1
        ? [`区块 ${index + 1}/${index + 2} 重叠 ${Math.round(overlap(current, next))}px`]
        : [];
    });
    return [...tableOverlaps, ...sectionOverlaps];
  });
  expect(overlapReport).toEqual([]);
}

test("operations shows mobile traces and sticky horizontal coverage", async ({ page }, testInfo) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/operations");
  await expect(page.getByRole("heading", { name: "设备运行状态" })).toBeVisible();
  await expect(page.getByLabel("轮次关键指标").getByText("20小时预计容量")).toBeVisible();
  await expect(page.locator(".quota-table th", { hasText: "滚动用量" })).toBeVisible();
  await expect(page.locator(".quota-table tbody tr").first()).toBeVisible();
  await expect(page.getByTestId("coverage-matrix").getByText("long_target_identity_for_mobile_layout_verification")).toBeVisible();
  await expect(page.getByRole("button", { name: /重试 phone-03/ })).toBeVisible();
  await page.getByRole("button", { name: "停止轮次" }).click();
  const dialog = page.getByRole("dialog", { name: "确认停止轮次" });
  await expect(dialog).toBeVisible();
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox).not.toBeNull();
  expect(dialogBox!.x).toBeGreaterThanOrEqual(0);
  expect(dialogBox!.y).toBeGreaterThanOrEqual(0);
  expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  expect(dialogBox!.y + dialogBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByRole("button", { name: "停止 phone-01-long-identity" }).click();
  await expect(page.getByRole("dialog", { name: "确认停止 phone-01-long-identity" })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

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
    await scroller.evaluate((element) => { element.scrollLeft = 0; });
  }
  await expectOperationsBandsDoNotOverlap(page);
  if (testInfo.project.name === "mobile") {
    const quotaFrame = page.locator(".compact-table-frame");
    expect(await quotaFrame.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
    const targetCell = page.locator(".coverage-table td.sticky-target").first();
    expect(await targetCell.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    const deviceRow = await page.locator(".device-row").first().boundingBox();
    expect(deviceRow).not.toBeNull();
    expect(deviceRow!.height).toBeLessThanOrEqual(240);
  } else {
    const deviceRow = await page.locator(".device-row").first().boundingBox();
    expect(deviceRow).not.toBeNull();
    expect(deviceRow!.y + deviceRow!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  }
  await expectNoViewportOverflow(page);
  await expectPageControlsDoNotOverlap(page);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("operations.png"), fullPage: true });
});

test("inbox exposes readiness and operator controls in a bounded drawer", async ({ page }, testInfo) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "线索转化" })).toBeVisible();
  await page.getByRole("button", { name: /打开 buyer_with_a_very_long/ }).click();
  await expect(page.getByText("私域渠道已配置")).toBeVisible();
  await expect(page.getByRole("button", { name: /人工接管/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "创建发送计划" })).toBeVisible();
  await expectNoViewportOverflow(page);
  await expectPageControlsDoNotOverlap(page);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("inbox.png"), fullPage: true });
});

test("analytics separates measured evidence from projection", async ({ page }, testInfo) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { name: "获客经营分析" })).toBeVisible();
  await expect(page.getByText("实测完成数")).toBeVisible();
  await expect(page.getByText("预计日容量")).toBeVisible();
  await expectNoViewportOverflow(page);
  await expectPageControlsDoNotOverlap(page);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("analytics.png"), fullPage: true });
});

test("navigation preserves direct routes and browser history", async ({ page }) => {
  const errors = rejectConsoleErrors(page);
  await page.goto("/operations");
  await page.getByRole("button", { name: "线索收件箱" }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "线索转化" })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/operations$/);
  await expect(page.getByRole("heading", { name: "设备运行状态" })).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "线索转化" })).toBeVisible();
  await page.getByRole("button", { name: "经营分析" }).click();
  await expect(page).toHaveURL(/\/analytics$/);
  expect(errors).toEqual([]);
});

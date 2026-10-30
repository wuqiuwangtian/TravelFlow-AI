import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
async function seed(page: Page) {
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  const latest = async () => {
    const session = await (
      await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
    ).json();
    return (
      await page.request.get(
        `${proxy}/api/versions/${session.versions[trip.branch_id].at(-1).id}`,
      )
    ).json();
  };
  const before = await latest();
  const changed = await page.request.post(`${proxy}/api/chat`, {
    data: {
      ...trip,
      message: "第2天轻松一点",
      request_id: crypto.randomUUID(),
    },
  });
  expect(changed.ok()).toBe(true);
  return { trip, before, latest, changed: await latest() };
}
async function budget(page: Page, id: string, title: string, amount: string) {
  const row = page.locator(`[id="journey-budget-row-stay-${id}"]`);
  const category = row.locator("xpath=ancestor::details[1]");
  if ((await category.getAttribute("open")) === null)
    await category.locator("summary").click();
  await row
    .getByRole("button", { name: `修改${title}预估`, exact: true })
    .click();
  const form = row.getByRole("form", { name: "修改预估费用", exact: true });
  await form
    .getByRole("textbox", {
      name: "预估总额（元，全团；留空待核实）",
      exact: true,
    })
    .fill(amount);
  await form.getByRole("button", { name: "保存预估", exact: true }).click();
  await expect(form).toHaveCount(0);
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});
for (const amount of ["125.50", ""]) {
  test(`人工预算${amount || "未知"}与实付在撤销后保留，回顾只汇总实际账目`, async ({
    page,
  }) => {
    const { trip, before, latest, changed } = await seed(page);
    const target = changed.itinerary.days[0].activities.find(
      (activity: { kind: string; is_context_anchor: boolean }) =>
        activity.kind === "hotel" && !activity.is_context_anchor,
    );
    await page.goto(`/chat/${trip.session_id}`);
    await budget(page, target.id, target.title, amount);
    const priced = await latest();
    const paid = await page.request.post(
      `${proxy}/api/versions/${priced.version.id}/expenses`,
      {
        data: {
          title: "住宿订金",
          amount: "20.25",
          category: "lodging",
          day: 2,
          activity_id: target.id,
          budget_item_id: `stay-${target.id}`,
          request_id: crypto.randomUUID(),
        },
      },
    );
    expect(paid.ok()).toBe(true);
    const current = await latest();
    await page.reload();
    const note = page.getByRole("region", {
      name: "规划修改记录",
      exact: true,
    });
    const undo = note.getByRole("button", {
      name: "撤销这次规划修改",
      exact: true,
    });
    await expect(undo).toBeEnabled();
    await undo.click();
    await expect(note.getByRole("status")).toContainText("已撤销规划修改");
    const result = await latest();
    expect(result.itinerary.budget_items).toEqual(
      current.itinerary.budget_items,
    );
    expect(result.itinerary.expenses).toEqual(current.itinerary.expenses);
    expect(
      result.itinerary.days[1].activities.map((a: { id: string }) => a.id),
    ).toEqual(
      before.itinerary.days[1].activities.map((a: { id: string }) => a.id),
    );
    const row = result.budget_overview.rows.find(
      (item: { id: string }) => item.id === `stay-${target.id}`,
    );
    expect(row.planned_cents).toBe(amount ? 12550 : null);
    const recap = await (
      await page.request.get(`${proxy}/api/versions/${result.version.id}/recap`)
    ).json();
    expect(recap.ledger.paid_cents).toBe(4025);
    expect(
      recap.days.flatMap((day: { completed: string[] }) => day.completed),
    ).not.toContain(target.title);
    for (const format of ["md", "html"]) {
      const response = await page.request.get(
        `${proxy}/api/versions/${result.version.id}/recap/export?format=${format}`,
      );
      expect(response.ok()).toBe(true);
      const text = await response.text();
      expect(text).toContain("40.25");
      expect(text).not.toContain("125.50");
      expect(
        (
          await page.request.get(
            `${proxy}/api/versions/${changed.version.id}/export?format=${format}`,
          )
        ).ok(),
      ).toBe(true);
    }
    expect((await latest()).version.id).toBe(result.version.id);
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
  });
}
test("新增站点关联人工预算后禁用直接撤销，保留站点及金额", async ({ page }) => {
  const { trip, before, latest, changed } = await seed(page);
  const oldIds = new Set(
    before.itinerary.days.flatMap((day: { activities: { id: string }[] }) =>
      day.activities.map((activity) => activity.id),
    ),
  );
  const target = changed.itinerary.days[1].activities.find(
    (activity: { id: string; is_context_anchor: boolean }) =>
      !oldIds.has(activity.id) && !activity.is_context_anchor,
  );
  expect(target).toBeTruthy();
  await page.goto(`/chat/${trip.session_id}`);
  await budget(page, target.id, target.title, "66.66");
  await page.reload();
  const note = page.getByRole("region", { name: "规划修改记录", exact: true });
  await expect(note).toContainText("新增站点已关联消费或人工预算");
  await expect(
    note.getByRole("button", { name: "撤销这次规划修改", exact: true }),
  ).toBeDisabled();
  const result = await latest();
  expect(
    result.itinerary.days[1].activities.some(
      (activity: { id: string }) => activity.id === target.id,
    ),
  ).toBe(true);
  expect(
    result.budget_overview.rows.find(
      (row: { id: string }) => row.id === `stay-${target.id}`,
    ).planned_cents,
  ).toBe(6666);
});

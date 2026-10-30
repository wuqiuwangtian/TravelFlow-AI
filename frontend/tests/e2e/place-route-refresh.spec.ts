import { expect, test } from "playwright/test";

const proxy = "/api/backend";

for (const entry of ["对话确认", "手动编辑"] as const) {
  test(`${entry}地点后两段旧交通变为未知，刷新和导出保留预约与账目`, async ({
    page,
  }) => {
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
      route.abort(),
    );
    const trip = await (
      await page.request.post(`${proxy}/test/places?connected=true`)
    ).json();
    const session = async () =>
      (
        await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
      ).json();
    const original = await session();
    const version = original.versions[trip.branch_id].at(-1).id;
    const before = await (
      await page.request.get(`${proxy}/api/versions/${version}`)
    ).json();
    expect(before.itinerary.days[0].legs[1]).toMatchObject({
      duration_seconds: 600,
      cost: 8,
    });
    await page.goto(`/chat/${trip.session_id}`);
    const timeline = page.getByLabel("当天行程", { exact: true });
    await expect(timeline).toBeVisible();
    if (entry === "对话确认") {
      await page
        .getByRole("button", { name: "确认第1天第2站的地点", exact: true })
        .click();
      const prompt = page.getByLabel("待补充的信息", { exact: true });
      await expect(
        prompt.getByRole("button", { name: /西单店.*西单路2号/ }),
      ).toBeVisible();
      await page.reload();
      await prompt.getByRole("button", { name: /西单店.*西单路2号/ }).click();
      await expect(prompt).toHaveCount(0);
    } else {
      await page.getByRole("button", { name: "更多细节", exact: true }).click();
      const lunch = timeline.locator(":scope > li").nth(1);
      await lunch
        .getByRole("button", { name: "手动编辑此站", exact: true })
        .click();
      await lunch
        .getByRole("textbox", { name: "地点名称", exact: true })
        .fill("示例餐厅(西单店)");
      await lunch
        .getByRole("button", { name: "保存并重算当天", exact: true })
        .click();
      await expect(
        lunch.getByRole("textbox", { name: "地点名称", exact: true }),
      ).toHaveCount(0);
    }
    await expect(timeline).toContainText("西单店");
    await page.reload();
    await expect(timeline).toContainText("已有预约 12:00");
    const saved = await session();
    expect(saved.versions[trip.branch_id]).toHaveLength(2);
    const detail = await (
      await page.request.get(
        `${proxy}/api/versions/${saved.versions[trip.branch_id].at(-1).id}`,
      )
    ).json();
    expect(detail.itinerary.days[0].legs).toHaveLength(3);
    for (const leg of detail.itinerary.days[0].legs.slice(0, 2)) {
      expect(leg.duration_seconds).toBeNull();
      expect(leg.cost).toBeNull();
    }
    expect(detail.itinerary.days[0].activities[2].booking).toEqual(
      before.itinerary.days[0].activities[2].booking,
    );
    expect(detail.itinerary.days[1]).toEqual(before.itinerary.days[1]);
    expect(detail.itinerary.expenses).toEqual(before.itinerary.expenses);
    expect(detail.itinerary.budget_items).toEqual(
      before.itinerary.budget_items,
    );
    await page
      .getByRole("region", { name: "行程待核对事项", exact: true })
      .locator("summary")
      .click();
    const notes = page.getByRole("region", {
      name: "时间与行程提醒",
      exact: true,
    });
    await expect(notes).toContainText("示例餐厅(西单店)");
    await expect(notes).toContainText("示例酒店");
    await expect(timeline).not.toContainText("约 10 分钟");
    for (const fmt of ["md", "html"]) {
      const response = await page.request.get(
        `${proxy}/api/versions/${detail.version.id}/export?format=${fmt}`,
      );
      expect(response.ok()).toBe(true);
      const exported = await response.text();
      expect(exported).toContain("西单店");
      expect(exported).toContain("12:00");
    }
    expect((await session()).versions[trip.branch_id]).toHaveLength(2);
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
  });
}

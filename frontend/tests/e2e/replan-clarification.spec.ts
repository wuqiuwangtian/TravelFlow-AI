import { expect, test } from "playwright/test";

const proxy = "/api/backend";
for (const { failed, mixed } of [
  { failed: false, mixed: false },
  { failed: true, mixed: false },
  { failed: false, mixed: true },
]) {
  test(`旅中补日期刷新后继续${failed ? "并从模型失败恢复" : mixed ? "并承接已补记账金额" : ""}，保留原时刻和账目`, async ({
    page,
  }) => {
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
      route.abort(),
    );
    const trip = await (
      await page.request.post(`${proxy}/test/places?replan=true`)
    ).json();
    const session = async () =>
      (
        await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
      ).json();
    const original = await session();
    const originalId = original.versions[trip.branch_id].at(-1).id;
    const before = await (
      await page.request.get(`${proxy}/api/versions/${originalId}`)
    ).json();
    await page.goto(`/chat/${trip.session_id}`);
    const message = `${mixed ? "午餐花了；" : ""}${failed ? `replan-error-${trip.session_id}：` : ""}现在09:45我在示例酒店，18:00前结束，调整后续安排，保留预约和账目`;
    await page
      .getByRole("textbox", { name: "旅行对话输入", exact: true })
      .fill(message);
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const prompt = page.getByLabel("待补充的信息", { exact: true });
    if (mixed) {
      await expect(prompt).toContainText("金额");
      await page
        .getByRole("textbox", { name: "旅行对话输入", exact: true })
        .fill("30元");
      await page.getByRole("button", { name: "发送", exact: true }).click();
    }
    await expect(prompt).toContainText("第几天");
    await expect(prompt).toContainText("09:45");
    await page.reload();
    const choices = prompt.getByRole("group", {
      name: "旅中调整日期选择",
      exact: true,
    });
    await expect(
      choices.getByRole("button", { name: "第2天", exact: true }),
    ).toBeVisible();
    expect((await session()).versions[trip.branch_id]).toHaveLength(1);
    await choices.getByRole("button", { name: "第2天", exact: true }).click();
    if (failed) {
      const retry = page.getByRole("button", {
        name: "重试这条消息",
        exact: true,
      });
      await expect(retry).toBeVisible();
      expect((await session()).versions[trip.branch_id]).toHaveLength(1);
      await retry.click();
    }
    await expect(prompt).toHaveCount(0);
    await page.reload();
    const saved = await session();
    expect(saved.versions[trip.branch_id]).toHaveLength(2);
    const detail = await (
      await page.request.get(
        `${proxy}/api/versions/${saved.versions[trip.branch_id].at(-1).id}`,
      )
    ).json();
    const day = detail.itinerary.days[1];
    expect(day.replan_context).toMatchObject({
      time: "09:45",
      end_time: "18:00",
    });
    expect(day.activities[0]).toMatchObject({
      start_time: "09:45",
      is_planning_origin: false,
      progress: "in_progress",
    });
    expect(day.activities[1].booking).toEqual(
      before.itinerary.days[1].activities[1].booking,
    );
    expect(detail.itinerary.days[0]).toEqual(before.itinerary.days[0]);
    if (mixed) {
      expect(detail.itinerary.expenses).toHaveLength(
        before.itinerary.expenses.length + 1,
      );
      expect(detail.itinerary.expenses[0]).toEqual(
        before.itinerary.expenses[0],
      );
      expect(detail.itinerary.expenses[1]).toMatchObject({
        amount_cents: 3000,
        day: null,
      });
    } else {
      expect(detail.itinerary.expenses).toEqual(before.itinerary.expenses);
    }
    expect(detail.itinerary.budget_items).toEqual(
      before.itinerary.budget_items,
    );
    await page.getByRole("button", { name: "第 2 天", exact: true }).click();
    await expect(page.getByLabel("当天行程", { exact: true })).toContainText(
      "09:45",
    );
    const attention = page.getByRole("region", {
      name: "行程待核对事项",
      exact: true,
    });
    await attention.locator("summary").click();
    await expect(
      attention.getByRole("region", { name: "时间与行程提醒", exact: true }),
    ).toContainText("冲突");
    for (const format of ["md", "html"]) {
      const response = await page.request.get(
        `${proxy}/api/versions/${detail.version.id}/export?format=${format}`,
      );
      expect(response.ok()).toBe(true);
      expect(await response.text()).toContain("09:45");
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

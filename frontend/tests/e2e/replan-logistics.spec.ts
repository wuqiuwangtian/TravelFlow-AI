import { expect, test } from "playwright/test";

const proxy = "/api/backend";
for (const entry of ["对话", "按钮"]) {
  test(`${entry}重排保留必要往返交通与住宿，显式取消只修改指定站点`, async ({
    page,
  }) => {
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
      route.abort(),
    );
    const trip = await (
      await page.request.post(`${proxy}/test/places?logistics=true`)
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
    async function send(text: string) {
      await page
        .getByRole("textbox", { name: "旅行对话输入", exact: true })
        .fill(text);
      const done = page.waitForResponse((response) =>
        response.url().endsWith("/api/chat/stream"),
      );
      await page.getByRole("button", { name: "发送", exact: true }).click();
      expect(await (await done).text()).toContain("event: done");
      await expect(
        page.getByRole("button", { name: "发送", exact: true }),
      ).toBeEnabled();
    }
    await page.getByRole("button", { name: "第 2 天", exact: true }).click();
    const timeline = page.getByLabel("当天行程", { exact: true });
    if (entry === "对话") {
      await send(
        "第2天14:00我在示例酒店，14:10前结束，只精简可选游览，保留预约和账目",
      );
    } else {
      await timeline.getByRole("button", { name: /规划酒店出发点/ }).click();
      const active = page.getByRole("region", {
        name: "当前站点",
        exact: true,
      });
      await active
        .getByRole("button", { name: "从这里调整后续", exact: true })
        .click();
      await active
        .getByLabel("从此处出发的时间", { exact: true })
        .fill("14:00");
      await active.getByLabel("最晚结束时间", { exact: true }).fill("14:10");
      await active
        .getByRole("button", { name: "更新后续安排", exact: true })
        .click();
      await expect(
        active.getByLabel("最晚结束时间", { exact: true }),
      ).toHaveCount(0);
    }
    await page.reload();
    await page.getByRole("button", { name: "第 2 天", exact: true }).click();
    for (const title of ["返程去北京南站", "高铁返上海", "过夜住宿"]) {
      await expect(timeline).toContainText(title);
    }
    await expect(timeline).not.toContainText("可选公园");
    const saved = await session();
    expect(saved.versions[trip.branch_id]).toHaveLength(2);
    const vid = saved.versions[trip.branch_id].at(-1).id;
    const detail = await (
      await page.request.get(`${proxy}/api/versions/${vid}`)
    ).json();
    const day = detail.itinerary.days[1];
    expect(
      day.activities.find(
        (activity: { id: string }) => activity.id === "overnight-hotel",
      ).cost,
    ).toBe(300);
    expect(
      day.activities.find(
        (activity: { id: string }) => activity.id === "dinner",
      ).booking,
    ).toEqual(before.itinerary.days[1].activities[1].booking);
    expect(
      day.legs.find((leg: { to_id: string }) => leg.to_id === "return-train")
        .cost,
    ).toBeNull();
    expect(detail.itinerary.days[0]).toEqual(before.itinerary.days[0]);
    expect(detail.itinerary.expenses).toEqual(before.itinerary.expenses);
    expect(detail.itinerary.budget_items).toEqual(
      before.itinerary.budget_items,
    );
    const attention = page.getByRole("region", {
      name: "行程待核对事项",
      exact: true,
    });
    await attention.locator("summary").click();
    await expect(
      attention.getByRole("region", { name: "时间与行程提醒", exact: true }),
    ).toContainText("时间冲突");
    for (const format of ["md", "html"]) {
      const response = await page.request.get(
        `${proxy}/api/versions/${vid}/export?format=${format}`,
      );
      expect(response.ok()).toBe(true);
      const text = await response.text();
      for (const word of ["高铁返上海", "过夜住宿", "时间冲突"])
        expect(text).toContain(word);
    }
    expect((await session()).versions[trip.branch_id]).toHaveLength(2);
    await send("第2天取消高铁返上海");
    await expect(timeline).not.toContainText("高铁返上海");
    await expect(timeline).toContainText("返程去北京南站");
    await expect(timeline).toContainText("过夜住宿");
    expect((await session()).versions[trip.branch_id]).toHaveLength(3);
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth + 1,
      ),
    ).toBe(true);
  });
}

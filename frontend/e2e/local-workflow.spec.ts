import { expect, test } from "@playwright/test";

test("runs the complete consent-gated local workflow without leaking sensitive data", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  const externalRequests: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname))
      externalRequests.push(request.url());
  });

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Podcast Intelligence" }),
  ).toBeVisible();
  await expect(
    page.getByText(/Sending transcript content requires/),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Synthetic evaluation episode/i })
    .click();
  await expect(
    page.getByRole("heading", { name: "Synthetic evaluation episode" }),
  ).toBeVisible();
  await expect(
    page.getByText("Use reproducible evaluation cases.").first(),
  ).toBeVisible();
  await expect(
    page.getByText("Exact synthetic evidence quote").first(),
  ).toBeVisible();

  await page.getByRole("button", { name: "Refresh analysis" }).click();
  const analysisDialog = page.getByRole("dialog", {
    name: "Send content to OpenAI?",
  });
  await expect(analysisDialog).toContainText("canonical transcript");
  await analysisDialog
    .getByRole("button", { name: "I consent, continue" })
    .click();
  await expect(
    page.getByText("Created and validated a new analysis."),
  ).toBeVisible();

  const question = page.getByLabel("Question");
  await question.fill("What does the episode recommend?");
  await page.getByRole("button", { name: "Ask question" }).click();
  const questionDialog = page.getByRole("dialog", {
    name: "Send content to OpenAI?",
  });
  await expect(questionDialog).toContainText("selected transcript excerpts");
  await questionDialog
    .getByRole("button", { name: "I consent, continue" })
    .click();
  await expect(
    page.getByText("The episode recommends reproducible evaluation cases."),
  ).toBeVisible();
  await page.getByText(/Observable trace/).click();
  await expect(page.getByText("search_transcript")).toBeVisible();
  await expect(page.getByText("call_fixture_search")).toBeVisible();
  expect(consoleErrors).toEqual([]);

  await question.fill("Trigger safe error");
  await page.getByRole("button", { name: "Ask question" }).click();
  await page.getByRole("button", { name: "I consent, continue" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Question answering failed safely.",
  );
  await expect(page.getByText("SENSITIVE FIXTURE PROVIDER DETAIL")).toHaveCount(
    0,
  );
  await expect(page.getByText("SENSITIVE FIXTURE ARGUMENT")).toHaveCount(0);
  await expect(
    page.getByText("The episode recommends reproducible evaluation cases."),
  ).toHaveCount(0);

  expect(consoleErrors).toHaveLength(1);
  expect(consoleErrors[0]).toContain("502");
  expect(externalRequests).toEqual([]);
});

test("keeps the workspace usable at a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .getByRole("button", { name: /Synthetic evaluation episode/i })
    .click();
  await expect(
    page.getByRole("heading", { name: "Synthetic evaluation episode" }),
  ).toBeVisible();
  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  await expect(
    page.getByRole("button", { name: "Refresh analysis" }),
  ).toBeVisible();
  await expect(page.getByLabel("Question")).toBeVisible();
});

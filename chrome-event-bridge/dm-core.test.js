const test = require("node:test");
const assert = require("node:assert/strict");

const dm = require("./dm-core.js");

test("normalizes message text consistently", () => {
  assert.equal(dm.normalizeText("  Hello\u3000  there  "), "Hello there");
});

test("builds a stable SHA-256 fingerprint from normalized fields", async () => {
  const input = {
    accountId: "account-01",
    conversationId: "messages:buyer",
    sender: "buyer",
    messageId: "visible-10",
    timestamp: "10:30",
    text: "  Hello   there ",
  };
  const normalized = {
    ...input,
    accountId: " account-01 ",
    text: "Hello there",
  };

  const fingerprint = await dm.fingerprintMessage(input);
  assert.equal(fingerprint, await dm.fingerprintMessage(normalized));
  assert.match(fingerprint, /^[a-f0-9]{64}$/);
});

test("accepts only latest inbound descriptors with text", () => {
  assert.equal(
    dm.isActionableInbound({ direction: "inbound", text: "hello", latest: true }),
    true,
  );
  assert.equal(
    dm.isActionableInbound({ direction: "inbound", text: "hello", latest: false }),
    false,
  );
  assert.equal(dm.isActionableInbound({ direction: "outbound", text: "hello" }), false);
  assert.equal(dm.isActionableInbound({ direction: "unknown", text: "hello" }), false);
  assert.equal(dm.isActionableInbound({ direction: "inbound", text: "  " }), false);
});

test("uses only TikTok Messages URLs as conversation keys", () => {
  assert.equal(
    dm.conversationKey("https://www.tiktok.com/messages/thread/123?lang=en", "Buyer.Name"),
    "https://www.tiktok.com/messages/thread/123",
  );
  assert.equal(
    dm.conversationKey("https://tiktok.com/messages/thread/123/", "Buyer.Name"),
    "https://www.tiktok.com/messages/thread/123",
  );
  assert.equal(dm.conversationKey("https://tiktok.com/@buyer", "Buyer.Name"), "user:buyer.name");
  assert.equal(dm.conversationKey("https://example.com/messages/thread/123", "Buyer.Name"), "user:buyer.name");
  assert.equal(dm.conversationKey("https://evil-tiktok.com/messages/thread/123", "Buyer.Name"), "user:buyer.name");
  assert.equal(dm.conversationKey("ftp://tiktok.com/messages/thread/123", "Buyer.Name"), "user:buyer.name");
  assert.equal(dm.conversationKey("not a url", "  Buyer.Name "), "user:buyer.name");
  assert.equal(dm.conversationKey("not a url", "  "), null);
});

test("compares inbound identity by normalized stable fields", () => {
  const message = {
    accountId: "account-01",
    conversationId: "messages:buyer",
    sender: "buyer",
    messageId: "visible-10",
    timestamp: "10:30",
    text: " Hello   there ",
  };
  assert.equal(dm.sameInbound(message, { ...message, text: "Hello there" }), true);
  assert.equal(dm.sameInbound(message, { ...message, messageId: "visible-11" }), false);
  assert.equal(dm.sameInbound(message, null), false);
});

test("finds exactly one visible semantic button", () => {
  const buttons = [
    { label: "Cancel", visible: true, id: "cancel" },
    { label: " Send ", visible: true, id: "send" },
    { label: "Send", visible: false, id: "hidden-send" },
  ];
  assert.equal(dm.findSemanticButton(buttons, ["send", "发送"]).id, "send");
  assert.equal(
    dm.findSemanticButton(
      [
        { label: "Send", visible: true },
        { label: "发送", visible: true },
      ],
      ["send", "发送"],
    ),
    null,
  );
  assert.equal(dm.findSemanticButton(buttons, ["reply"]), null);
});

test("ignores hidden DOM semantic buttons", () => {
  const hidden = {
    textContent: "Send",
    getAttribute() { return null; },
    getClientRects() { return []; },
  };
  const visible = {
    textContent: "Send",
    getAttribute() { return null; },
    getClientRects() { return [{}]; },
  };
  assert.equal(dm.findSemanticButton([hidden, visible], ["send"]), visible);
});

test("reconciles only an exact normalized outbound bubble", () => {
  assert.equal(
    dm.hasMatchingOutbound("Thanks for asking", [
      { direction: "inbound", text: "Hi" },
      { direction: "outbound", text: " Thanks   for asking " },
    ]),
    true,
  );
  assert.equal(
    dm.hasMatchingOutbound("Thanks for asking", [
      { direction: "outbound", text: "Thanks for asking!" },
    ]),
    false,
  );
  assert.equal(
    dm.hasMatchingOutbound("Thanks for asking", [
      { direction: "inbound", text: "Thanks for asking" },
    ]),
    false,
  );
});

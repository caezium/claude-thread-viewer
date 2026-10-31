// Synthetic state only: these tests never start a browser or read local sessions.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");

const source = readFileSync(`${__dirname}/web/app.js`, "utf8");

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function harness() {
  const body = deferred();
  const bodyRequested = deferred();
  const copies = [],
    downloads = [];
  const nodes = new Map();
  const node = () => ({
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {},
    replaceChildren() {},
    append() {},
    remove() {},
    focus() {},
    select() {},
    click() {
      downloads.push(this.download);
    },
  });
  const sandbox = {
    URLSearchParams,
    location: { hash: "", pathname: "/" },
    history: { replaceState() {} },
    sessionStorage: { getItem: () => "synthetic-test-key", setItem() {} },
    document: {
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, node());
        return nodes.get(id);
      },
      createElement: node,
      body: node(),
      addEventListener() {},
    },
    window: { addEventListener() {} },
    setInterval() {},
    setTimeout() {},
    URL: { createObjectURL: () => "blob:synthetic-test", revokeObjectURL() {} },
    navigator: {
      clipboard: {
        async writeText(text) {
          copies.push(text);
        },
      },
    },
    fetch: async (path) => {
      // Hold the unrelated initial list refresh; it cannot affect this export.
      if (path === "/api/threads") return new Promise(() => {});
      return {
        ok: true,
        text() {
          bodyRequested.resolve();
          return body.promise;
        },
        blob() {
          bodyRequested.resolve();
          return body.promise;
        },
      };
    },
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context);
  vm.runInContext(
    'selected = "thread-a"; current = {id: "thread-a", title: "Synthetic example", messages: [{}]};',
    context,
  );
  return {
    run: (code) => vm.runInContext(code, context),
    body,
    bodyRequested,
    copies,
    downloads,
  };
}

test("handoff is not copied if the selection changes while its body loads", async () => {
  const h = harness();
  const exporting = h.run('exportThread("handoff")');
  await h.bodyRequested.promise;
  h.run('selected = "thread-b"; requestNumber += 1;');
  h.body.resolve("Synthetic handoff");
  await exporting;
  assert.deepEqual(h.copies, []);
});

test("an invalidated thread cannot finish an in-flight handoff", async () => {
  const h = harness();
  const exporting = h.run('exportThread("handoff")');
  await h.bodyRequested.promise;
  h.run('invalidate("Thread was archived");');
  h.body.resolve("Synthetic handoff");
  await exporting;
  assert.deepEqual(h.copies, []);
});

test("a download is cancelled if the selection changes while its body loads", async () => {
  const h = harness();
  const exporting = h.run('exportThread("markdown")');
  await h.bodyRequested.promise;
  h.run('selected = "thread-b"; requestNumber += 1;');
  h.body.resolve({});
  await exporting;
  assert.deepEqual(h.downloads, []);
});

test("an unchanged active selection can download a conversation", async () => {
  const h = harness();
  const exporting = h.run('exportThread("markdown")');
  await h.bodyRequested.promise;
  h.body.resolve({});
  await exporting;
  assert.deepEqual(h.downloads, ["Synthetic-example.md"]);
});

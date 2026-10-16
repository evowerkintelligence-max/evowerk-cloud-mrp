// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { EditorView } from "@codemirror/view";
import { currentCompletions, startCompletion } from "@codemirror/autocomplete";
import { SqlCodeEditor } from "./SqlCodeEditor";

// jsdom has no layout; CodeMirror measures selection ranges once a selection moves.
beforeAll(() => {
  const empty = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as unknown as DOMRectList;
  Range.prototype.getClientRects = empty;
  Range.prototype.getBoundingClientRect = () => new DOMRect();
});
afterEach(cleanup);

describe("SqlCodeEditor", () => {
  it("mounts a CodeMirror editor showing the given SQL", () => {
    const { container } = render(
      <SqlCodeEditor value="SELECT qty FROM data" onChange={() => {}} columns={[{ name: "qty", dtype: "numeric" }]} />,
    );
    expect(container.querySelector(".cm-content")!.textContent).toBe("SELECT qty FROM data");
  });

  it("offers dataset columns as completions that insert a typed cast", async () => {
    const { container } = render(
      <SqlCodeEditor
        value="SELECT q"
        onChange={() => {}}
        columns={[
          { name: "qty", dtype: "numeric" },
          { name: "quoted_at", dtype: "datetime" },
          { name: "quality", dtype: "text" },
        ]}
      />,
    );
    const view = EditorView.findFromDOM(container.querySelector(".cm-editor") as HTMLElement)!;
    view.dispatch({ selection: { anchor: view.state.doc.length } });
    startCompletion(view);

    await waitFor(() => expect(currentCompletions(view.state).length).toBeGreaterThan(0));
    const byLabel = Object.fromEntries(currentCompletions(view.state).map((c) => [c.label, c]));
    expect(byLabel.qty).toMatchObject({ detail: "numeric", apply: "CAST(qty AS DOUBLE)" });
    expect(byLabel.quoted_at).toMatchObject({ detail: "datetime", apply: "CAST(quoted_at AS TIMESTAMP)" });
    expect(byLabel.quality).toMatchObject({ detail: "text", apply: "quality" });
  });

  it("reports edits through onChange", () => {
    const onChange = vi.fn();
    const { container } = render(<SqlCodeEditor value="SELECT 1" onChange={onChange} columns={[]} />);
    const view = EditorView.findFromDOM(container.querySelector(".cm-editor") as HTMLElement)!;
    view.dispatch({ changes: { from: view.state.doc.length, insert: "0" } });
    expect(onChange).toHaveBeenLastCalledWith("SELECT 10", expect.anything());
  });
});

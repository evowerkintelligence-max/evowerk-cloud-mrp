// @vitest-environment jsdom
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage } from "@/test/renderPage";
import { ItemSelect } from "./ItemSelect";

const ITEMS = [
  { id: "i1", sku: "BOLT", name: "Bolt" },
  { id: "i2", sku: "NUT", name: "Nut" },
];

function Harness({ onChange, excludeId }: { onChange: (id: string) => void; excludeId?: string }) {
  const [value, setValue] = useState("");
  return (
    <ItemSelect
      items={ITEMS}
      value={value}
      excludeId={excludeId}
      required
      onChange={(id) => {
        setValue(id);
        onChange(id);
      }}
    />
  );
}

describe("ItemSelect", () => {
  it("lists items minus the excluded one and reports the picked id", async () => {
    mockApi({});
    const onChange = vi.fn();
    renderPage(<Harness onChange={onChange} excludeId="i2" />);
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Select an item", "BOLT - Bolt", "+ New item..."]);

    await userEvent.selectOptions(screen.getByRole("combobox"), "i1");
    expect(onChange).toHaveBeenLastCalledWith("i1");
  });

  it("quick-creates a new item and selects it", async () => {
    const api = mockApi({ "POST /items": { id: "i9", sku: "WASHER", name: "Washer" } });
    const onChange = vi.fn();
    renderPage(<Harness onChange={onChange} />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "+ New item...");
    expect(onChange).toHaveBeenLastCalledWith("");

    const create = screen.getByRole("button", { name: "Create" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    // While creating, the hidden mirror keeps the form's required check.
    expect((screen.getByLabelText("Item") as HTMLInputElement).checkValidity()).toBe(false);
    await userEvent.type(screen.getByPlaceholderText("SKU"), "WASHER");
    await userEvent.type(screen.getByPlaceholderText("Name"), "Washer");
    await userEvent.click(create);

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("i9"));
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({ sku: "WASHER", name: "Washer" });
    expect(screen.getByRole("combobox")).toBeTruthy();
  });

  it("shows the server's error when creating fails, and Cancel returns to the dropdown", async () => {
    mockApi({ "POST /items": new Response(JSON.stringify({ detail: "SKU already exists" }), { status: 409 }) });
    renderPage(<Harness onChange={() => {}} />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "+ New item...");
    await userEvent.type(screen.getByPlaceholderText("SKU"), "BOLT");
    await userEvent.type(screen.getByPlaceholderText("Name"), "Bolt again");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("SKU already exists")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("combobox")).toBeTruthy();
    expect(screen.queryByText("SKU already exists")).toBeNull();
  });
});

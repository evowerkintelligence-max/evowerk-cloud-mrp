import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

interface ItemOption {
  id: string;
  sku: string;
  name: string;
}

const NEW_ITEM_VALUE = "__new__";

// Item dropdown used wherever a feature needs to reference an item (BOM
// components, PO lines, ...). Picking "+ New item" swaps in a 2-field quick
// create instead of sending the user to the Items catalog and back.
export function ItemSelect({
  id,
  items,
  value,
  onChange,
  excludeId,
  required,
}: {
  id?: string;
  items: ItemOption[] | undefined;
  value: string;
  onChange: (itemId: string) => void;
  excludeId?: string;
  required?: boolean;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");

  const createItem = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/items", {
        method: "POST",
        body: JSON.stringify({ sku, name }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create item");
      }
      return res.json() as Promise<ItemOption>;
    },
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: ["items"] });
      onChange(item.id);
      setAdding(false);
      setSku("");
      setName("");
    },
  });

  if (adding) {
    return (
      <div className="flex flex-wrap items-start gap-2">
        <Input
          placeholder="SKU"
          value={sku}
          onChange={(e) => setSku(e.target.value)}
          className="w-28"
          autoFocus
        />
        <Input
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="min-w-[140px] flex-1"
        />
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={createItem.isPending}
          disabled={!sku || !name}
          onClick={() => createItem.mutate()}
        >
          Create
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            setAdding(false);
            createItem.reset();
          }}
        >
          Cancel
        </Button>
        {createItem.isError && (
          <p className="w-full text-xs text-destructive">{(createItem.error as Error).message}</p>
        )}
      </div>
    );
  }

  return (
    <Select
      id={id}
      value={value}
      required={required}
      onChange={(e) => {
        if (e.target.value === NEW_ITEM_VALUE) setAdding(true);
        else onChange(e.target.value);
      }}
    >
      <option value="" disabled>
        Select an item
      </option>
      {items
        ?.filter((item) => item.id !== excludeId)
        .map((item) => (
          <option key={item.id} value={item.id}>
            {item.sku} - {item.name}
          </option>
        ))}
      <option value={NEW_ITEM_VALUE}>+ New item...</option>
    </Select>
  );
}

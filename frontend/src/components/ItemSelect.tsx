import { useEffect, useRef, useState } from "react";
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
  const requiredMirrorRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    requiredMirrorRef.current?.setCustomValidity(
      required && !value ? "Select an item, or finish creating the new one." : "",
    );
  }, [required, value]);

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
        {/* Swapping the <select> out for these inputs would otherwise drop
            the surrounding form's `required` coverage for this field -
            mirror it here so submitting mid-create still gets blocked.
            Zero-size but left in normal flow (not sr-only's absolute clip)
            so the native validation bubble anchors right here, next to the
            SKU/Name inputs, instead of off in a detached corner. */}
        <input
          ref={requiredMirrorRef}
          id={id}
          aria-label="Item"
          required={required}
          value={value}
          onChange={() => {}}
          tabIndex={-1}
          style={{ position: "static", width: 0, height: 0, padding: 0, margin: 0, border: 0, opacity: 0 }}
        />
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
            setSku("");
            setName("");
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
        if (e.target.value === NEW_ITEM_VALUE) {
          onChange("");
          setAdding(true);
        } else {
          onChange(e.target.value);
        }
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

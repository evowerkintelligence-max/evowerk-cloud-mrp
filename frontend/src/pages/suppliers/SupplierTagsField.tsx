import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";

export function SupplierTagsField({
  tagInput,
  setTagInput,
  addTag,
  editTags,
  removeTag,
}: {
  tagInput: string;
  setTagInput: (value: string) => void;
  addTag: () => void;
  editTags: string[];
  removeTag: (tag: string) => void;
}) {
  return (
    <Field label="Tags" htmlFor="edit-tag-input">
      <div className="flex gap-2">
        <Input
          id="edit-tag-input"
          value={tagInput}
          onChange={(e) => setTagInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addTag();
            }
          }}
          placeholder="Add a tag and press Enter"
        />
        <Button type="button" variant="outline" onClick={addTag}>
          Add
        </Button>
      </div>
      {editTags.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-2">
          {editTags.map((t) => (
            <Badge
              key={t}
              variant="neutral"
              className="cursor-pointer"
              onClick={() => removeTag(t)}
            >
              {t} ×
            </Badge>
          ))}
        </div>
      )}
    </Field>
  );
}

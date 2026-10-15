import { describe, expect, it } from "vitest";
import { friendlySqlError } from "./QueryPanels";

describe("friendlySqlError", () => {
  it("strips DuckDB error-class labels", () => {
    expect(friendlySqlError('Query failed: Binder Error: Referenced column "x" not found')).toBe(
      'Query failed: Referenced column "x" not found',
    );
    expect(friendlySqlError("Query failed: parser error: syntax error at end")).toBe("Query failed: syntax error at end");
  });

  it("leaves other messages untouched", () => {
    expect(friendlySqlError("Query failed: timeout")).toBe("Query failed: timeout");
    expect(friendlySqlError("Binder Error: x")).toBe("Binder Error: x");
  });
});

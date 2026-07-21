import { describe, expect, it } from "vitest";
import { blankConfigDraft, draftToConfig, configToDraft } from "./stepConfig";

describe("draftToConfig", () => {
  it("builds a load_dataset config", () => {
    const draft = { ...blankConfigDraft(), datasetId: "ds-1", limit: "250" };
    expect(draftToConfig("load_dataset", draft)).toEqual({ dataset_id: "ds-1", limit: 250 });
  });

  it("builds a filter config reading from a dataset", () => {
    const draft = {
      ...blankConfigDraft(),
      source: "dataset" as const,
      datasetId: "ds-1",
      limit: "500",
      filters: [{ column: "region", op: "eq" as const, value: "west" }],
    };
    expect(draftToConfig("filter", draft)).toEqual({
      dataset_id: "ds-1",
      filters: [{ column: "region", op: "eq", value: "west" }],
      limit: 500,
    });
  });

  it("builds a filter config reading from an upstream step, splitting 'in' values on comma", () => {
    const draft = {
      ...blankConfigDraft(),
      source: "upstream" as const,
      inputStep: "load",
      limit: "500",
      filters: [{ column: "sku", op: "in" as const, value: "A-1, A-2 ,A-3" }],
    };
    expect(draftToConfig("filter", draft)).toEqual({
      input_step: "load",
      filters: [{ column: "sku", op: "in", value: ["A-1", "A-2", "A-3"] }],
      limit: 500,
    });
  });

  it("builds an aggregate config with select rows", () => {
    const draft = {
      ...blankConfigDraft(),
      source: "dataset" as const,
      datasetId: "ds-1",
      limit: "500",
      groupBy: ["region"],
      select: [{ column: "qty", agg: "sum" as const, alias: "total_qty" }],
    };
    expect(draftToConfig("aggregate", draft)).toEqual({
      dataset_id: "ds-1",
      group_by: ["region"],
      select: [{ column: "qty", agg: "sum", alias: "total_qty" }],
      limit: 500,
    });
  });

  it("builds an export_result config", () => {
    const draft = { ...blankConfigDraft(), inputStep: "filter_west", format: "json" as const };
    expect(draftToConfig("export_result", draft)).toEqual({ input_step: "filter_west", format: "json" });
  });

  it("builds an import_csv config", () => {
    const draft = { ...blankConfigDraft(), contentBase64: "Zm9v", filename: "stock.csv", name: "My import" };
    expect(draftToConfig("import_csv", draft)).toEqual({
      content_base64: "Zm9v",
      filename: "stock.csv",
      name: "My import",
    });
  });
});

describe("configToDraft", () => {
  it("round-trips a dataset-sourced filter config", () => {
    const config = {
      dataset_id: "ds-1",
      filters: [{ column: "region", op: "eq", value: "west" }],
      limit: 500,
    };
    const draft = configToDraft("filter", config);
    expect(draft.source).toBe("dataset");
    expect(draft.datasetId).toBe("ds-1");
    expect(draft.filters).toEqual([{ column: "region", op: "eq", value: "west" }]);
    expect(draftToConfig("filter", draft)).toEqual(config);
  });

  it("round-trips an upstream-sourced filter config with an 'in' list value", () => {
    const config = { input_step: "load", filters: [{ column: "sku", op: "in", value: ["A-1", "A-2"] }], limit: 500 };
    const draft = configToDraft("filter", config);
    expect(draft.source).toBe("upstream");
    expect(draft.inputStep).toBe("load");
    expect(draft.filters).toEqual([{ column: "sku", op: "in", value: "A-1, A-2" }]);
    expect(draftToConfig("filter", draft)).toEqual(config);
  });

  it("defaults an unknown aggregation function to sum when parsing", () => {
    const draft = configToDraft("aggregate", { dataset_id: "ds-1", group_by: [], select: [{ column: "qty" }], limit: 500 });
    expect(draft.select).toEqual([{ column: "qty", agg: "sum", alias: "" }]);
  });

  it("returns a blank draft for an unrecognized step type", () => {
    expect(configToDraft("sql", {})).toEqual({ ...blankConfigDraft(), datasetId: "", sql: "", limit: "500" });
  });
});

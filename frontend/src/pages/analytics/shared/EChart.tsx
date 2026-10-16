import { useEffect, useRef } from "react";
import * as echarts from "echarts";

// ─── EChart: tiny React wrapper (init + resize + dispose) ────────────────────

export function EChart({
  option,
  height = 300,
  className,
}: {
  option: echarts.EChartsOption;
  height?: number | string;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts>();

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, null, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = undefined;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(
      {
        backgroundColor: "transparent",
        animation: true,
        animationDuration: 700,
        animationEasing: "cubicOut",
        ...option,
      },
      true,
    );
  }, [option]);

  return <div ref={ref} className={className} style={{ height }} />;
}

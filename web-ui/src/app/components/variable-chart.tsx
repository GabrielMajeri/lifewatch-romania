"use client";

import ReactECharts from "echarts-for-react";
import _ from "lodash";

type Props = {
  title: string;
  variableName: string;
  units?: string;
  timestamps: Date[];
  measurements: number[];
};

export default function VariableChart({
  title,
  variableName,
  units,
  timestamps,
  measurements,
}: Props) {
  const option = {
    title: {
      text: title,
    },
    tooltip: {
      trigger: "axis",
      position: function (pt: [Date, number][]) {
        return [pt[0], "10%"];
      },
    },
    xAxis: {
      type: "time",
    },
    yAxis: {
      type: "value",
      axisLabel: {
        formatter: `{value} ${units ?? ""}`,
      },
    },
    series: {
      name: variableName,
      type: "line",
      smooth: false,
      symbol: "none",
      data: _.zip(timestamps, measurements),
    },
  };
  return (
    <ReactECharts
      className="my-3"
      option={option} //theme="dark"
    />
  );
}

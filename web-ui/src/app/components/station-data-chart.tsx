"use client";

import ReactECharts from "echarts-for-react";

type Props = {
  data: {
    variables: string[];
    timestamps: Record<string, number[]>;
    measurements: Record<string, number[]>;
  };
};

export default function StationDataChart({ data }: Props) {
  const variable = data.variables[0];

  const option = {
    title: {
      text: `${variable}`,
    },
    xAxis: {
      data: data.timestamps[variable],
    },
    yAxis: {},
    series: {
      name: variable,
      type: "line",
      data: data.measurements[variable],
    },
  };
  return <ReactECharts className="my-3" option={option} theme="dark" />;
}

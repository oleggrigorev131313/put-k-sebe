export interface ChartPoint {
  label: string;
  value: number;
}

export function Chart({
  points,
  ariaLabel,
}: {
  points: ChartPoint[];
  ariaLabel: string;
}) {
  const width = 320;
  const height = 168;
  const pad = { l: 8, r: 10, t: 16, b: 28 };
  const max = Math.max(1, ...points.map((point) => point.value));
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const coords = points.map((point, index) => {
    const x = pad.l + (points.length === 1 ? innerW / 2 : (index * innerW) / (points.length - 1));
    const y = pad.t + (1 - point.value / max) * innerH;
    return { ...point, x, y };
  });
  const line = coords.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const area = `${line} L${coords[coords.length - 1]?.x ?? 0},${pad.t + innerH} L${coords[0]?.x ?? 0},${pad.t + innerH} Z`;
  const labelStep = points.length > 16 ? Math.ceil(points.length / 6) : points.length > 8 ? 2 : 1;
  const showDots = points.length <= 14;

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        {[0.25, 0.5, 0.75, 1].map((step) => {
          const y = pad.t + (1 - step) * innerH;
          return <line key={step} x1={pad.l} x2={width - pad.r} y1={y} y2={y} stroke="#E6E3DC" strokeWidth="1" />;
        })}
        <path className="chart-area" d={area} />
        <path className="chart-line" pathLength={1} d={line} />
        {showDots
          ? coords.map((point) => <circle key={point.label + point.x} className="chart-dot" cx={point.x} cy={point.y} r="3.2" />)
          : null}
        {coords.map((point, index) =>
          index % labelStep === 0 || index === coords.length - 1 ? (
            <text key={point.label + index} className="chart-label" x={point.x} y={height - 8} textAnchor="middle">
              {point.label}
            </text>
          ) : null,
        )}
      </svg>
    </div>
  );
}

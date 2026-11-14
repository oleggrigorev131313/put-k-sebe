import mark from "../assets/logo-mark.png";

type Variant = "volumetric" | "mono" | "outline";

export function Logo({
  variant = "volumetric",
  height = 72,
  className = "",
}: {
  variant?: Variant;
  height?: number;
  className?: string;
}) {
  const width = Math.round(height * 1.44);
  if (variant === "outline") {
    return (
      <svg
        className={className}
        width={width}
        height={height}
        viewBox="0 0 144 100"
        aria-hidden="true"
      >
        <g fill="none" stroke="currentColor" strokeWidth="8">
          <ellipse cx="58" cy="52" rx="44" ry="36" transform="rotate(-16 58 52)" />
          <ellipse cx="90" cy="48" rx="44" ry="36" transform="rotate(14 90 48)" />
        </g>
      </svg>
    );
  }
  return (
    <img
      className={className}
      src={mark}
      width={width}
      height={height}
      alt=""
      draggable={false}
      style={variant === "mono" ? { filter: "grayscale(1) contrast(1.05)" } : undefined}
    />
  );
}

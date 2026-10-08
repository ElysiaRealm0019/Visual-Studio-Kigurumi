import { useState } from "react";
import { Stack, Switch } from "../../../ui/mui";
import type { BrowControlKey, EditRecipe } from "../deformation/recipe";
import { browControlRanges } from "../deformation/recipe";
import { ParameterSlider } from "./ParameterSlider";

type BrowControlConfig = {
  actualMax: number;
  key: BrowControlKey;
  label: string;
};

const browControls: BrowControlConfig[] = [
  { actualMax: 0.3, key: "browVertical", label: "上下" },
  { actualMax: 0.3, key: "browThickness", label: "粗细" },
  { actualMax: 0.3, key: "browLength", label: "长短" },
  { actualMax: 0.3, key: "browSpacing", label: "间距" },
  { actualMax: 0.3, key: "browInnerSpacing", label: "眉头间距" },
  { actualMax: 0.3, key: "browTilt", label: "倾斜" },
  { actualMax: 0.3, key: "browArch", label: "眉峰" },
];

export type BrowControlsProps = {
  values: EditRecipe["brows"];
  onChange: (key: BrowControlKey, value: number) => void;
  onReset: (key: BrowControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: () => void;
};

/** Symmetric controls: the slider shows -1..1 and maps to ±actualMax (doubled when the range is expanded). */
export function BrowControls({ values, onChange, onReset, onSliderInteractionEnd, onSliderInteractionStart }: BrowControlsProps) {
  const [expanded, setExpanded] = useState(false);
  const factor = expanded ? 2 : 1;

  return (
    <Stack gap="md">
      {browControls.map((control) => {
        const scale = control.actualMax * factor;
        const precision = browControlRanges[control.key].precision;
        return (
          <ParameterSlider
            dataTestId={`brow-control-${control.key}`}
            key={control.key}
            label={control.label}
            max={1}
            min={-1}
            precision={2}
            step={0.01}
            value={Number(((values[control.key] ?? 0) / scale).toFixed(2))}
            onChange={(value) => onChange(control.key, Number((value * scale).toFixed(precision)))}
            onInteractionEnd={onSliderInteractionEnd}
            onInteractionStart={onSliderInteractionStart}
            onReset={() => onReset(control.key)}
          />
        );
      })}
      <Switch checked={expanded} label="扩大参数范围" onChange={(event) => setExpanded(event.currentTarget.checked)} />
    </Stack>
  );
}

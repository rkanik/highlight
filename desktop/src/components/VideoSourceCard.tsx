import { useRef, useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Grid,
  Group,
  Loader,
  Paper,
  RangeSlider,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import {
  IconArrowBarToLeft,
  IconArrowBarToRight,
  IconPlus,
  IconTrash,
  IconX,
  IconArrowsHorizontal,
} from "@tabler/icons-react";
import { baseName, fileSrc, fmtTime } from "../api/tauri";
import type { ScanRange, SourceItem } from "../types";

type Props = {
  source: SourceItem;
  disabled: boolean;
  onRemove: () => void;
  onRanges: (ranges: ScanRange[]) => void;
};

export default function VideoSourceCard({ source, disabled, onRemove, onRanges }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playhead, setPlayhead] = useState(0);
  const { duration, ranges } = source;
  const covered = ranges.reduce((acc, [a, b]) => acc + Math.max(0, b - a), 0);
  const pct = (t: number) => (duration ? `${(100 * t) / duration}%` : "0%");

  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) v.currentTime = Math.max(0, Math.min(t, duration));
  };

  const update = (i: number, r: ScanRange) => {
    const next = ranges.slice();
    next[i] = [Math.min(r[0], r[1] - 1), Math.max(r[1], r[0] + 1)];
    onRanges(next);
  };

  const addRange = () => {
    const start = Math.floor(Math.min(playhead, Math.max(0, duration - 60)));
    const end = Math.min(duration, start + Math.max(60, Math.floor(duration * 0.1)));
    onRanges([...ranges, [start, end]]);
  };

  return (
    <Paper p="md" radius="lg" withBorder bg="var(--hl-panel)">
      <Group justify="space-between" mb="sm" wrap="nowrap">
        <Stack gap={0} style={{ minWidth: 0 }}>
          <Text fw={600} truncate="end" title={source.path}>
            {baseName(source.path)}
          </Text>
          <Text size="xs" c="dimmed" truncate="end">
            {source.path}
          </Text>
        </Stack>
        <Group gap="xs" wrap="nowrap">
          {source.probing ? (
            <Loader size="xs" />
          ) : source.error ? (
            <Badge color="red" variant="light">
              probe failed
            </Badge>
          ) : (
            <>
              <Badge variant="light" color="steel">
                {fmtTime(duration)}
              </Badge>
              {source.width > 0 && (
                <Badge variant="light" color="steel">
                  {source.width}×{source.height}
                </Badge>
              )}
            </>
          )}
          <Tooltip label="Remove video">
            <ActionIcon variant="subtle" color="gray" onClick={onRemove} disabled={disabled}>
              <IconX size={18} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <video
            ref={videoRef}
            className="source-video"
            src={fileSrc(source.path)}
            controls
            preload="metadata"
            onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
          />
          <div
            className="range-timeline"
            style={{ marginTop: 10 }}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              seek(((e.clientX - rect.left) / rect.width) * duration);
            }}
            title="Click to seek"
          >
            {ranges.map(([a, b], i) => (
              <div
                key={i}
                className="seg"
                style={{ left: pct(a), width: `calc(${pct(b)} - ${pct(a)})` }}
              />
            ))}
            <div className="head" style={{ left: pct(playhead) }} />
          </div>
          <Group justify="space-between" mt={6}>
            <Text size="xs" c="dimmed">
              Playhead {fmtTime(playhead)}
            </Text>
            <Text size="xs" c="dimmed">
              Scanning {fmtTime(covered)} of {fmtTime(duration)}
            </Text>
          </Group>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 6 }}>
          <Stack gap="sm">
            <Group justify="space-between">
              <Text size="sm" fw={600} tt="uppercase" c="dimmed" lts={1}>
                Scan ranges
              </Text>
              <Group gap={6}>
                <Button
                  size="compact-xs"
                  variant="subtle"
                  leftSection={<IconArrowsHorizontal size={14} />}
                  disabled={disabled || !duration}
                  onClick={() => onRanges([[0, duration]])}
                >
                  Full VOD
                </Button>
                <Button
                  size="compact-xs"
                  variant="light"
                  leftSection={<IconPlus size={14} />}
                  disabled={disabled || !duration}
                  onClick={addRange}
                >
                  Add range
                </Button>
              </Group>
            </Group>

            {!duration && !source.probing && (
              <Text size="sm" c="dimmed">
                Duration unknown — full video will be scanned.
              </Text>
            )}

            {ranges.map((r, i) => (
              <Paper key={i} p="sm" radius="md" bg="var(--hl-panel-2)">
                <Group justify="space-between" mb={8} wrap="nowrap">
                  <Text size="xs" fw={600}>
                    #{i + 1} · {fmtTime(r[0])} – {fmtTime(r[1])}
                    <Text span size="xs" c="dimmed">
                      {"  "}({fmtTime(r[1] - r[0])})
                    </Text>
                  </Text>
                  <Group gap={2} wrap="nowrap">
                    <Tooltip label="Start at playhead">
                      <ActionIcon
                        size="sm"
                        variant="subtle"
                        disabled={disabled}
                        onClick={() => update(i, [Math.floor(playhead), r[1]])}
                      >
                        <IconArrowBarToLeft size={15} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="End at playhead">
                      <ActionIcon
                        size="sm"
                        variant="subtle"
                        disabled={disabled}
                        onClick={() => update(i, [r[0], Math.ceil(playhead)])}
                      >
                        <IconArrowBarToRight size={15} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Remove range">
                      <ActionIcon
                        size="sm"
                        variant="subtle"
                        color="red"
                        disabled={disabled || ranges.length <= 1}
                        onClick={() => onRanges(ranges.filter((_, j) => j !== i))}
                      >
                        <IconTrash size={15} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Group>
                <RangeSlider
                  min={0}
                  max={duration || 1}
                  step={1}
                  minRange={1}
                  value={r}
                  disabled={disabled || !duration}
                  label={fmtTime}
                  onChange={(v) => update(i, v)}
                  onChangeEnd={(v) => seek(v[0])}
                />
              </Paper>
            ))}
          </Stack>
        </Grid.Col>
      </Grid>
    </Paper>
  );
}

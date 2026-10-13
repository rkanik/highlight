import { useState } from "react";
import {
  Badge,
  Button,
  Checkbox,
  Group,
  Modal,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconPlayerPlayFilled, IconScissors } from "@tabler/icons-react";
import { baseName, fileSrc, fmtTime } from "../api/tauri";
import { useStore } from "../state/store";
import type { Clip } from "../types";

const KIND_LABEL: Record<string, string> = {
  my_kill: "Kill",
  my_knock: "Knock",
  my_assist: "Assist",
};

function kindBadges(kind: string) {
  const parts = kind.split("+").filter(Boolean);
  const counts = new Map<string, number>();
  for (const p of parts) counts.set(p, (counts.get(p) ?? 0) + 1);
  return [...counts.entries()].map(([k, n]) => (
    <Badge
      key={k}
      size="xs"
      variant="light"
      color={k === "my_kill" ? "red" : k === "my_knock" ? "ember" : "teal"}
    >
      {n > 1 ? `${n}× ` : ""}
      {KIND_LABEL[k] ?? k}
    </Badge>
  ));
}

function ClipCard({
  clip,
  selected,
  onToggle,
  onPlay,
}: {
  clip: Clip;
  selected: boolean;
  onToggle: () => void;
  onPlay: () => void;
}) {
  const portrait = clip.aspect === "9:16";
  return (
    <Paper
      radius="md"
      bg="var(--hl-panel-2)"
      className={`clip-card ${selected ? "selected" : "off"}`}
      style={{ overflow: "hidden" }}
    >
      <div className={`clip-thumb ${portrait ? "portrait" : ""}`} onClick={onPlay}>
        <video src={`${fileSrc(clip.path)}#t=0.6`} preload="metadata" muted />
        <div className="play">
          <IconPlayerPlayFilled size={34} color="#fff" />
        </div>
      </div>
      <Stack gap={6} p="xs">
        <Group justify="space-between" wrap="nowrap">
          <Checkbox
            checked={selected}
            onChange={onToggle}
            label={<Text size="xs" fw={600}>#{clip.index}</Text>}
          />
          <Text size="xs" c="dimmed">
            {clip.dur.toFixed(1)}s
          </Text>
        </Group>
        <Group gap={4}>
          {clip.role === "cold_open" && (
            <Badge size="xs" variant="filled" color="ember">
              Cold open
            </Badge>
          )}
          {kindBadges(clip.kind)}
          {clip.scoped && (
            <Badge size="xs" variant="outline" color="steel">
              Scoped
            </Badge>
          )}
        </Group>
        <Text size="xs" c="dimmed" truncate="end" title={clip.video}>
          {baseName(clip.video)} @ {fmtTime(clip.start)}
        </Text>
      </Stack>
    </Paper>
  );
}

export default function ClipGrid() {
  const { job, toggleClip, setSelected, startAssemble } = useStore();
  const [playing, setPlaying] = useState<Clip | null>(null);
  const { clips, selected } = job;
  const sel = new Set(selected);
  const chosen = clips.filter((c) => sel.has(c.id));
  const total = chosen.reduce((a, c) => a + c.dur, 0);
  const cold = clips.filter((c) => c.role === "cold_open");
  const body = clips.filter((c) => c.role !== "cold_open");

  const section = (label: string, list: Clip[]) =>
    list.length > 0 && (
      <Stack gap="xs">
        <Text size="xs" fw={600} tt="uppercase" c="dimmed" lts={1}>
          {label} · {list.length}
        </Text>
        <SimpleGrid cols={{ base: 2, md: 3, xl: 5 }} spacing="sm">
          {list.map((c) => (
            <ClipCard
              key={c.id}
              clip={c}
              selected={sel.has(c.id)}
              onToggle={() => toggleClip(c.id)}
              onPlay={() => setPlaying(c)}
            />
          ))}
        </SimpleGrid>
      </Stack>
    );

  return (
    <Paper radius="lg" withBorder bg="var(--hl-panel)" p="md">
      <Group justify="space-between" mb="md" wrap="wrap">
        <Stack gap={0}>
          <Title order={4}>Review clips</Title>
          <Text size="sm" c="dimmed">
            {chosen.length} of {clips.length} selected · {fmtTime(total)} final length
          </Text>
        </Stack>
        <Group gap="xs">
          <Button size="xs" variant="subtle" onClick={() => setSelected(clips.map((c) => c.id))}>
            Select all
          </Button>
          <Button size="xs" variant="subtle" color="gray" onClick={() => setSelected([])}>
            Clear
          </Button>
          <Button
            leftSection={<IconScissors size={16} />}
            disabled={!chosen.length || job.running}
            loading={job.running && job.mode === "assemble"}
            onClick={() => void startAssemble()}
          >
            Assemble {chosen.length} clip{chosen.length === 1 ? "" : "s"}
          </Button>
        </Group>
      </Group>
      <Stack gap="lg">
        {section("Cold open", cold)}
        {section("Fight clips", body)}
      </Stack>

      <Modal
        opened={!!playing}
        onClose={() => setPlaying(null)}
        size={playing?.aspect === "9:16" ? "md" : "xl"}
        centered
        title={
          playing && (
            <Group gap="xs">
              <Text fw={600}>Clip #{playing.index}</Text>
              {kindBadges(playing.kind)}
            </Group>
          )
        }
      >
        {playing && (
          <Stack>
            <video
              src={fileSrc(playing.path)}
              controls
              autoPlay
              style={{ width: "100%", maxHeight: "72vh", background: "#000", borderRadius: 8 }}
            />
            {playing.text && (
              <Text size="xs" c="dimmed" lineClamp={3}>
                {playing.text}
              </Text>
            )}
            <Group justify="space-between">
              <Text size="sm" c="dimmed">
                {baseName(playing.video)} · {fmtTime(playing.start)}–{fmtTime(playing.end)}
              </Text>
              <Checkbox
                checked={sel.has(playing.id)}
                onChange={() => toggleClip(playing.id)}
                label="Include in final"
              />
            </Group>
          </Stack>
        )}
      </Modal>
    </Paper>
  );
}

import { Stack, Text, Title } from "@mantine/core";
import { IconMovie } from "@tabler/icons-react";
import { useStore } from "../state/store";

export default function DropOverlay() {
  const { dragHot } = useStore();
  if (!dragHot) return null;
  return (
    <div className="drop-overlay">
      <Stack align="center" gap="xs" className="drop-overlay-inner">
        <IconMovie size={56} stroke={1.3} color="var(--hl-ember)" />
        <Title order={2}>Drop to add videos</Title>
        <Text c="dimmed">mp4 · mkv · mov · avi</Text>
      </Stack>
    </div>
  );
}

import { Group, Stack, Text } from "@mantine/core";
import { IconMovie } from "@tabler/icons-react";
import { fileSrc, fmtTime } from "../api/tauri";
import type { OutputItem } from "../types";

export default function OutputTile({ item, onOpen }: { item: OutputItem; onOpen: () => void }) {
  const thumb = item.thumb_yt || item.thumb;
  const dur = Number(item.meta?.duration_seconds ?? 0);
  const template = String(item.meta?.template ?? "");
  return (
    <div className="lib-tile" onClick={onOpen} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen()}>
      <div style={{ overflow: "hidden" }}>
        {thumb ? (
          <img src={fileSrc(thumb, item.modified)} alt="" loading="lazy" />
        ) : (
          <div style={{ aspectRatio: "16 / 9", display: "grid", placeItems: "center", background: "#000" }}>
            <IconMovie size={32} color="#555" />
          </div>
        )}
      </div>
      <Stack gap={2} p="sm">
        <Text size="sm" fw={600} lineClamp={2}>
          {item.title}
        </Text>
        <Group gap="xs">
          <Text size="xs" c="dimmed">
            {new Date(item.modified * 1000).toLocaleDateString()}
          </Text>
          {dur > 0 && (
            <Text size="xs" c="dimmed">
              · {fmtTime(dur)}
            </Text>
          )}
          {template && (
            <Text size="xs" c="dimmed">
              · {template}
            </Text>
          )}
        </Group>
      </Stack>
    </div>
  );
}

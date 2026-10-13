import { Button, Stack, Text, Title } from "@mantine/core";
import { IconFolderOpen, IconUpload } from "@tabler/icons-react";
import { useStore } from "../state/store";

type Props = { compact?: boolean };

export default function DropArea({ compact = false }: Props) {
  const { browseSources, dragHot } = useStore();
  return (
    <div
      className={`drop-zone ${dragHot ? "hot" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => void browseSources()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") void browseSources();
      }}
    >
      <Stack align="center" gap={compact ? 6 : "sm"} py={compact ? "md" : 56} px="lg">
        <IconUpload
          className="drop-icon"
          size={compact ? 28 : 52}
          stroke={1.4}
          color="var(--hl-ember)"
        />
        <Title order={compact ? 5 : 3} ta="center">
          {compact ? "Add more videos" : "Drop gameplay VODs"}
        </Title>
        {!compact && (
          <Text c="dimmed" ta="center" maw={420}>
            Long recordings welcome. Pick several, trim scan ranges per video, review clips before the
            final cut.
          </Text>
        )}
        {!compact && (
          <Button
            mt="xs"
            leftSection={<IconFolderOpen size={18} />}
            onClick={(e) => {
              e.stopPropagation();
              void browseSources();
            }}
          >
            Browse files
          </Button>
        )}
      </Stack>
    </div>
  );
}

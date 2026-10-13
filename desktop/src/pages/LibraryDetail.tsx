import { useEffect, useState, type ReactNode } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Center,
  Container,
  CopyButton,
  Grid,
  Group,
  Image,
  Loader,
  Paper,
  Stack,
  Text,
  Textarea,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconArrowLeft, IconCheck, IconCopy, IconFolderOpen } from "@tabler/icons-react";
import { api, fileSrc, fmtTime } from "../api/tauri";
import { useStore } from "../state/store";
import type { OutputItem } from "../types";

function Copy({ value, label }: { value: string; label: string }) {
  return (
    <CopyButton value={value} timeout={1600}>
      {({ copied, copy }) => (
        <Tooltip label={copied ? "Copied" : label}>
          <ActionIcon variant="light" color={copied ? "teal" : "gray"} onClick={copy}>
            {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
          </ActionIcon>
        </Tooltip>
      )}
    </CopyButton>
  );
}

function Field({ label, value, children }: { label: string; value: string; children?: ReactNode }) {
  return (
    <Stack gap={4}>
      <Group justify="space-between">
        <Text size="xs" fw={600} tt="uppercase" c="dimmed" lts={1}>
          {label}
        </Text>
        <Copy value={value} label={`Copy ${label.toLowerCase()}`} />
      </Group>
      {children ?? (
        <Text size="sm" style={{ wordBreak: "break-all" }}>
          {value}
        </Text>
      )}
    </Stack>
  );
}

export default function LibraryDetail({ dir }: { dir: string }) {
  const { navigate } = useStore();
  const [item, setItem] = useState<OutputItem | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .readOutput(dir)
      .then(setItem)
      .catch((e) => setError(String(e)));
  }, [dir]);

  const back = (
    <Button variant="subtle" color="gray" leftSection={<IconArrowLeft size={16} />} onClick={() => navigate("library")} w="fit-content">
      Library
    </Button>
  );

  if (error)
    return (
      <Container py="xl">
        {back}
        <Text c="red" mt="md">
          {error}
        </Text>
      </Container>
    );
  if (!item)
    return (
      <Center py={120}>
        <Loader />
      </Center>
    );

  const meta = item.meta ?? {};
  const counts = (meta.counts ?? {}) as Record<string, number>;
  const portrait = String(meta.aspect ?? "") === "9:16";

  return (
    <Container fluid px="xl" py="lg">
      <Stack gap="lg">
        {back}
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={6} style={{ minWidth: 0 }}>
            <Group gap="xs" wrap="nowrap">
              <Title order={2} lineClamp={2}>
                {item.title}
              </Title>
              <Copy value={item.title} label="Copy title" />
            </Group>
            <Group gap={6}>
              {meta.template != null && <Badge variant="light">{String(meta.template)}</Badge>}
              {meta.duration_seconds != null && (
                <Badge variant="light" color="steel">
                  {fmtTime(Number(meta.duration_seconds))}
                </Badge>
              )}
              {meta.clip_count != null && (
                <Badge variant="light" color="steel">
                  {String(meta.clip_count)} clips
                </Badge>
              )}
              {Object.entries(counts).map(([k, n]) =>
                n ? (
                  <Badge key={k} variant="outline" color="gray">
                    {n} {k.replace("my_", "")}
                  </Badge>
                ) : null
              )}
              {meta.ai_provider != null && (
                <Badge variant="light" color="teal">
                  AI · {String(meta.ai_provider)}
                </Badge>
              )}
            </Group>
          </Stack>
          <Button variant="light" leftSection={<IconFolderOpen size={16} />} onClick={() => void api.openPath(item.dir)}>
            Show folder
          </Button>
        </Group>

        <Grid gutter="lg">
          <Grid.Col span={{ base: 12, lg: portrait ? 5 : 8 }}>
            {item.mp4 ? (
              <video
                className="detail-video"
                src={fileSrc(item.mp4, item.modified)}
                poster={item.thumb ? fileSrc(item.thumb, item.modified) : undefined}
                controls
                style={portrait ? { aspectRatio: "9 / 16", maxHeight: "78vh", objectFit: "contain" } : undefined}
              />
            ) : (
              <Paper p="xl" bg="var(--hl-panel)">
                <Text c="dimmed">No mp4 in this folder.</Text>
              </Paper>
            )}
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: portrait ? 7 : 4 }}>
            <Paper p="md" radius="lg" withBorder bg="var(--hl-panel)">
              <Stack gap="lg">
                {item.mp4 && <Field label="Video path" value={item.mp4} />}
                <Field label="Folder" value={item.dir} />
                <Field label="Description" value={item.description}>
                  <Textarea
                    value={item.description}
                    readOnly
                    autosize
                    minRows={6}
                    maxRows={16}
                    styles={{ input: { fontSize: 13 } }}
                  />
                </Field>
                <Stack gap="xs">
                  <Text size="xs" fw={600} tt="uppercase" c="dimmed" lts={1}>
                    Thumbnails
                  </Text>
                  <Group align="flex-start" gap="sm" grow>
                    {[item.thumb, item.thumb_yt].filter(Boolean).map((t) => (
                      <Stack key={t} gap={4}>
                        <Image
                          src={fileSrc(t, item.modified)}
                          radius="md"
                          fit="contain"
                          mah={260}
                          bg="#000"
                          style={{ cursor: "pointer" }}
                          onClick={() => t && void api.openPath(t)}
                        />
                        <Group justify="space-between" wrap="nowrap">
                          <Text size="xs" c="dimmed" truncate="end">
                            {t!.split(/[\\/]/).pop()}
                          </Text>
                          <Copy value={t!} label="Copy thumbnail path" />
                        </Group>
                      </Stack>
                    ))}
                  </Group>
                </Stack>
              </Stack>
            </Paper>
          </Grid.Col>
        </Grid>
      </Stack>
    </Container>
  );
}

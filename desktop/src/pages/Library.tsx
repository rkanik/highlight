import { useEffect, useState } from "react";
import {
  ActionIcon,
  Center,
  Container,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconFolderOpen, IconRefresh, IconSearch } from "@tabler/icons-react";
import OutputTile from "../components/OutputTile";
import { api } from "../api/tauri";
import { useStore } from "../state/store";
import type { OutputItem } from "../types";

export default function Library() {
  const { navigate, effectiveOutputDir, outputsVersion, appInfo } = useStore();
  const [items, setItems] = useState<OutputItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!appInfo) return;
    setItems(null);
    api
      .listOutputs(effectiveOutputDir)
      .then((list) => {
        setItems(list);
        setError(null);
      })
      .catch((e) => {
        setItems([]);
        setError(String(e));
      });
  }, [effectiveOutputDir, outputsVersion, tick, appInfo]);

  const q = query.trim().toLowerCase();
  const shown = (items ?? []).filter((i) => !q || i.title.toLowerCase().includes(q));

  return (
    <Container fluid px="xl" py="lg">
      <Stack gap="lg">
        <Group justify="space-between" align="flex-end">
          <Stack gap={4}>
            <Title order={2}>Library</Title>
            <Text c="dimmed" size="sm" truncate="end">
              {effectiveOutputDir ?? "…"}
            </Text>
          </Stack>
          <Group gap="xs">
            <TextInput
              placeholder="Search titles"
              leftSection={<IconSearch size={16} />}
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
              w={240}
            />
            <Tooltip label="Refresh">
              <ActionIcon variant="light" size="lg" onClick={() => setTick((t) => t + 1)}>
                <IconRefresh size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Open output folder">
              <ActionIcon
                variant="light"
                size="lg"
                disabled={!effectiveOutputDir}
                onClick={() => effectiveOutputDir && void api.openPath(effectiveOutputDir)}
              >
                <IconFolderOpen size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>

        {items === null ? (
          <Center py={80}>
            <Loader />
          </Center>
        ) : shown.length === 0 ? (
          <Center py={80}>
            <Stack align="center" gap={4}>
              <Text fw={600}>{q ? "No matches" : "No videos yet"}</Text>
              <Text c="dimmed" size="sm">
                {error ?? (q ? "Try another search." : "Generated packages land here.")}
              </Text>
            </Stack>
          </Center>
        ) : (
          <SimpleGrid cols={{ base: 2, md: 3, xl: 4 }} spacing="lg">
            {shown.map((o) => (
              <OutputTile key={o.dir} item={o} onOpen={() => navigate("library", o.dir)} />
            ))}
          </SimpleGrid>
        )}
      </Stack>
    </Container>
  );
}

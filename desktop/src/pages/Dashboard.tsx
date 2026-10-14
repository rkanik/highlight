import { useEffect, useState } from "react";
import { Anchor, Button, Container, Group, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import DropArea from "../components/DropArea";
import OutputTile from "../components/OutputTile";
import { api } from "../api/tauri";
import { useStore } from "../state/store";
import type { OutputItem } from "../types";

export default function Dashboard() {
  const { sources, navigate, effectiveOutputDir, outputsVersion, appInfo } = useStore();
  const [recent, setRecent] = useState<OutputItem[]>([]);

  useEffect(() => {
    if (!appInfo) return;
    void api
      .listOutputs(effectiveOutputDir)
      .then((items) => setRecent(items.slice(0, 4)))
      .catch(() => setRecent([]));
  }, [effectiveOutputDir, outputsVersion, appInfo]);

  return (
    <div className="dash-hero">
      <Container size="md" py={64}>
        <Stack gap="xl">
          <Stack gap="xs">
            <Title className="dash-title">
              <span>HILVOX</span>
            </Title>
            <Text size="lg" c="dimmed" maw={520}>
              Drop a long match. Get punchy, branded shorts back — scanned on your GPU, reviewed by you.
            </Text>
          </Stack>

          <DropArea />

          {sources.length > 0 && (
            <Group justify="space-between">
              <Text c="dimmed">
                {sources.length} video{sources.length === 1 ? "" : "s"} waiting in Generate.
              </Text>
              <Button
                variant="light"
                rightSection={<IconArrowRight size={16} />}
                onClick={() => navigate("generate")}
              >
                Continue
              </Button>
            </Group>
          )}

          {recent.length > 0 && (
            <Stack gap="sm" mt="lg">
              <Group justify="space-between">
                <Title order={4}>Recent cuts</Title>
                <Anchor size="sm" onClick={() => navigate("library")}>
                  Open library
                </Anchor>
              </Group>
              <SimpleGrid cols={{ base: 2, md: 4 }} spacing="md">
                {recent.map((o) => (
                  <OutputTile key={o.dir} item={o} onOpen={() => navigate("library", o.dir)} />
                ))}
              </SimpleGrid>
            </Stack>
          )}
        </Stack>
      </Container>
    </div>
  );
}

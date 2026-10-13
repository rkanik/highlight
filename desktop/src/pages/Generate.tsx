import { Alert, Button, Container, Group, Stack, Stepper, Text, Title } from "@mantine/core";
import {
  IconAlertTriangle,
  IconCheck,
  IconFolderOpen,
  IconPlayerStopFilled,
  IconRadar,
  IconMovie,
} from "@tabler/icons-react";
import ClipGrid from "../components/ClipGrid";
import DropArea from "../components/DropArea";
import LogPanel from "../components/LogPanel";
import VideoSourceCard from "../components/VideoSourceCard";
import { api, baseName } from "../api/tauri";
import { useStore } from "../state/store";

export default function Generate() {
  const { sources, removeSource, setRanges, job, startScan, cancelJob, navigate } = useStore();
  const step =
    job.phase === "done" ? 3 : job.phase === "review" || job.phase === "assembling" ? 2 : sources.length ? 1 : 0;
  const probing = sources.some((s) => s.probing);
  const scanned = job.clips.length > 0;

  if (!sources.length) {
    return (
      <Container size="sm" py={80}>
        <Stack gap="lg">
          <Title order={2}>Generate</Title>
          <Text c="dimmed">Add one or more gameplay videos to start.</Text>
          <DropArea />
        </Stack>
      </Container>
    );
  }

  return (
    <Container fluid px="xl" py="lg">
      <Stack gap="lg">
        <Group justify="space-between" align="flex-end" wrap="wrap">
          <Stack gap={4}>
            <Title order={2}>Generate</Title>
            <Text c="dimmed" size="sm">
              {sources.length} video{sources.length === 1 ? "" : "s"} · set scan ranges, scan, review, assemble
            </Text>
          </Stack>
          <Group gap="xs">
            {job.running ? (
              <Button color="red" variant="light" leftSection={<IconPlayerStopFilled size={16} />} onClick={() => void cancelJob()}>
                Stop
              </Button>
            ) : (
              <Button
                size="md"
                leftSection={<IconRadar size={18} />}
                disabled={probing}
                variant={scanned ? "light" : "filled"}
                onClick={() => void startScan()}
              >
                {scanned ? "Rescan" : "Scan videos"}
              </Button>
            )}
          </Group>
        </Group>

        <Stepper active={step} size="sm" allowNextStepsSelect={false}>
          <Stepper.Step label="Pick" description="Videos + ranges" />
          <Stepper.Step label="Scan" description="Detect + cut clips" loading={job.phase === "scanning"} />
          <Stepper.Step label="Review" description="Choose clips" loading={job.phase === "assembling"} />
          <Stepper.Step label="Output" description="Video package" />
        </Stepper>

        {job.phase === "failed" && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />} title="Scan failed">
            Check the log below. No events usually means username / clan tag mismatch or a range without fights.
          </Alert>
        )}

        {job.phase === "done" && job.outputDir && (
          <Alert color="teal" variant="light" icon={<IconCheck size={18} />} title="Video ready">
            <Group justify="space-between" wrap="wrap">
              <Text size="sm">{baseName(job.outputDir)}</Text>
              <Group gap="xs">
                <Button size="xs" leftSection={<IconMovie size={14} />} onClick={() => navigate("library", job.outputDir)}>
                  Open in library
                </Button>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconFolderOpen size={14} />}
                  onClick={() => job.outputDir && void api.openPath(job.outputDir)}
                >
                  Show folder
                </Button>
              </Group>
            </Group>
          </Alert>
        )}

        {scanned && <ClipGrid />}

        <Stack gap="md">
          {sources.map((s) => (
            <VideoSourceCard
              key={s.path}
              source={s}
              disabled={job.running}
              onRemove={() => removeSource(s.path)}
              onRanges={(r) => setRanges(s.path, r)}
            />
          ))}
          {!job.running && <DropArea compact />}
        </Stack>

        <LogPanel />
      </Stack>
    </Container>
  );
}

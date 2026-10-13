import { useEffect, useRef, useState } from "react";
import { ActionIcon, Button, Group, Paper, Progress, ScrollArea, Text, Tooltip } from "@mantine/core";
import { IconArrowDown, IconCopy, IconTrash } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useStore } from "../state/store";

const STICK_PX = 40;

export default function LogPanel() {
  const { job, clearLog } = useStore();
  const viewport = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);

  useEffect(() => {
    const el = viewport.current;
    if (el && stick) el.scrollTop = el.scrollHeight;
  }, [job.log, stick]);

  const jump = () => {
    const el = viewport.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setStick(true);
  };

  return (
    <Paper radius="lg" withBorder bg="var(--hl-panel)" p="md">
      <Group justify="space-between" mb="xs">
        <Text size="sm" fw={600} tt="uppercase" c="dimmed" lts={1}>
          Engine log
        </Text>
        <Group gap={4}>
          <Tooltip label="Copy log">
            <ActionIcon
              variant="subtle"
              color="gray"
              onClick={() => {
                void navigator.clipboard.writeText(job.log.join("\n"));
                notifications.show({ message: "Log copied" });
              }}
            >
              <IconCopy size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Clear log">
            <ActionIcon variant="subtle" color="gray" onClick={clearLog} disabled={job.running}>
              <IconTrash size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      {(job.running || job.pct > 0) && (
        <>
          <Progress value={Math.min(100, job.pct)} size="sm" animated={job.running} mb={6} />
          <Text size="xs" c="dimmed" mb="xs" lineClamp={1}>
            {job.status}
          </Text>
        </>
      )}
      <div style={{ position: "relative" }}>
        <ScrollArea
          h={220}
          viewportRef={viewport}
          type="auto"
          onScrollPositionChange={({ y }) => {
            const el = viewport.current;
            if (!el) return;
            setStick(el.scrollHeight - el.clientHeight - y < STICK_PX);
          }}
        >
          <div className="log-view">
            {job.log.length ? (
              job.log.map((l, i) => (
                <div
                  key={i}
                  className={/error|FAILED|stderr/i.test(l) ? "err" : /done|complete|ready/i.test(l) ? "ok" : undefined}
                >
                  {l}
                </div>
              ))
            ) : (
              <Text size="xs" c="dimmed">
                Nothing yet. Start a scan to see engine progress.
              </Text>
            )}
          </div>
        </ScrollArea>
        {!stick && (
          <Button
            size="compact-xs"
            variant="filled"
            leftSection={<IconArrowDown size={14} />}
            onClick={jump}
            style={{ position: "absolute", right: 12, bottom: 8 }}
          >
            Latest
          </Button>
        )}
      </div>
    </Paper>
  );
}

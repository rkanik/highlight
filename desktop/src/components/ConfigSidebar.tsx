import {
  Accordion,
  Alert,
  Anchor,
  Checkbox,
  Divider,
  NumberInput,
  ScrollArea,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { IconAlertTriangle, IconSparkles } from "@tabler/icons-react";
import { useStore } from "../state/store";
import { PROVIDER_PRESETS, type AppSettings } from "../types";

export default function ConfigSidebar() {
  const { config, setConfig, updateSettings, games, probe, job, navigate } = useStore();
  const s = config.settings;
  const locked = job.running;
  const templates = games.find((g) => g.id === s.gameId)?.templates ?? [];
  const cpuForced = s.gpuId === "cpu" || s.encode === "libx264";

  const gpuOptions = [
    { value: "auto", label: `Auto (${probe?.auto_select ?? "…"})` },
    ...(probe?.gpus ?? []).map((g) => ({ value: g.id, label: `${g.name} · ${g.type}` })),
    { value: "cpu", label: "CPU" },
  ];
  const decodeOptions = [...new Set([...(probe?.hwaccels ?? ["cuda"]), "cpu"])];
  const encodeOptions = probe?.ffmpeg_encoders ?? ["h264_nvenc", "libx264"];

  const providerOptions = [
    { value: "off", label: "Off — engine defaults" },
    ...config.providers.map((p) => ({
      value: p.id,
      label: `${p.name} · ${PROVIDER_PRESETS[p.type].label}`,
    })),
  ];
  const aiOn = !!config.activeProviderId && config.providers.some((p) => p.id === config.activeProviderId);

  const check = (key: keyof AppSettings, label: string) => (
    <Checkbox
      label={label}
      checked={!!s[key]}
      disabled={locked}
      onChange={(e) => updateSettings({ [key]: e.currentTarget.checked } as Partial<AppSettings>)}
    />
  );

  return (
    <ScrollArea h="100vh" type="hover">
      <Stack p="md" gap="md">
        <Title order={4}>Job config</Title>

        <Select
          label="Game"
          data={games.length ? games.map((g) => ({ value: g.id, label: g.name })) : [{ value: "pubg", label: "PUBG" }]}
          value={s.gameId}
          disabled={locked}
          allowDeselect={false}
          onChange={(v) => {
            const g = games.find((x) => x.id === v);
            updateSettings({ gameId: v ?? s.gameId, templateId: g?.templates[0]?.id ?? s.templateId });
          }}
        />
        <Select
          label="Template"
          description={templates.find((t) => t.id === s.templateId)?.description}
          data={
            templates.length
              ? templates.map((t) => ({ value: t.id, label: t.name }))
              : [{ value: s.templateId, label: s.templateId }]
          }
          value={s.templateId}
          disabled={locked}
          allowDeselect={false}
          onChange={(v) => v && updateSettings({ templateId: v })}
        />

        <Stack gap={6}>
          <Text size="sm" fw={500}>
            Include
          </Text>
          {check("includeKnocks", "My knocks")}
          {check("includeKills", "My kills")}
          {check("includeAssists", "My assists")}
        </Stack>

        <Select
          label="Detection"
          value={s.detectionMode}
          disabled={locked}
          allowDeselect={false}
          data={[
            { value: "feed_ocr", label: "Kill feed OCR" },
            { value: "hybrid", label: "Hybrid (feed + audio)" },
            { value: "audio_assist", label: "Audio only" },
          ]}
          onChange={(v) => v && updateSettings({ detectionMode: v as AppSettings["detectionMode"] })}
        />

        <Stack gap={6}>
          <Text size="sm" fw={500}>
            Padding (seconds)
          </Text>
          <NumberInput
            label="Before"
            size="xs"
            min={0}
            max={30}
            step={0.5}
            decimalScale={1}
            value={s.padBefore}
            disabled={locked}
            onChange={(v) => updateSettings({ padBefore: Number(v) || 0 })}
          />
          <NumberInput
            label="After"
            size="xs"
            min={0}
            max={30}
            step={0.5}
            decimalScale={1}
            value={s.padAfter}
            disabled={locked}
            onChange={(v) => updateSettings({ padAfter: Number(v) || 0 })}
          />
        </Stack>

        <TextInput
          label="Title override"
          placeholder="hook | PUBG — blank = AI / auto"
          value={s.title}
          disabled={locked}
          onChange={(e) => updateSettings({ title: e.currentTarget.value })}
        />

        <Divider label="AI" labelPosition="left" />
        <Select
          label="Provider"
          leftSection={<IconSparkles size={16} />}
          data={providerOptions}
          value={aiOn ? config.activeProviderId : "off"}
          disabled={locked}
          allowDeselect={false}
          onChange={(v) => setConfig((c) => ({ ...c, activeProviderId: !v || v === "off" ? null : v }))}
        />
        {config.providers.length === 0 && (
          <Text size="xs" c="dimmed">
            No providers yet.{" "}
            <Anchor size="xs" onClick={() => navigate("settings")}>
              Add an API key in Settings
            </Anchor>
          </Text>
        )}
        {aiOn && (
          <Stack gap={6}>
            {check("aiTitle", "Generate title")}
            {check("aiDescription", "Generate description")}
            {check("aiThumbnail", "Generate thumbnail")}
            <Text size="xs" c="dimmed">
              Runs at assemble. Falls back to built-in title / gameplay thumb on failure.
            </Text>
          </Stack>
        )}

        <Accordion variant="separated" radius="md">
          <Accordion.Item value="device">
            <Accordion.Control>Device</Accordion.Control>
            <Accordion.Panel>
              <Stack gap="sm">
                <Select
                  label="GPU"
                  data={gpuOptions}
                  value={s.gpuId}
                  disabled={locked}
                  allowDeselect={false}
                  onChange={(v) => v && updateSettings({ gpuId: v })}
                />
                <Select
                  label="Decode"
                  data={decodeOptions}
                  value={s.decode}
                  disabled={locked}
                  allowDeselect={false}
                  onChange={(v) => v && updateSettings({ decode: v })}
                />
                <Select
                  label="Encode"
                  data={encodeOptions}
                  value={s.encode}
                  disabled={locked}
                  allowDeselect={false}
                  onChange={(v) => v && updateSettings({ encode: v })}
                />
                {cpuForced && (
                  <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />} p="xs">
                    CPU encode can overheat this PC on long jobs. Prefer GPU.
                  </Alert>
                )}
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item value="paths">
            <Accordion.Control>Paths</Accordion.Control>
            <Accordion.Panel>
              <Stack gap="sm">
                <TextInput
                  label="Work dir"
                  placeholder="default: work/"
                  value={s.workDir}
                  disabled={locked}
                  onChange={(e) => updateSettings({ workDir: e.currentTarget.value })}
                />
                <TextInput
                  label="Output dir"
                  placeholder="default: out/"
                  value={s.outputDir}
                  disabled={locked}
                  onChange={(e) => updateSettings({ outputDir: e.currentTarget.value })}
                />
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      </Stack>
    </ScrollArea>
  );
}

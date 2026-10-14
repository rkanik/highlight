import { useState, type ReactNode } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Container,
  Group,
  Modal,
  Paper,
  PasswordInput,
  Radio,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconEdit, IconPlus, IconRestore, IconTrash, IconUpload } from "@tabler/icons-react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { notifications } from "@mantine/notifications";
import { api, fileSrc } from "../api/tauri";
import { useStore } from "../state/store";
import { PROVIDER_PRESETS, type AiProvider, type AiProviderType, type BrandAssets } from "../types";

const BRAND: { kind: keyof BrandAssets; label: string; hint: string }[] = [
  { kind: "character", label: "Character", hint: "AI reference for face / outfit / pose" },
  { kind: "cover", label: "Cover", hint: "AI style reference + thumb fallback" },
  { kind: "profile", label: "Logo", hint: "Composited once on every thumbnail" },
];

function Section({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  return (
    <Paper p="lg" radius="lg" withBorder bg="var(--hl-panel)">
      <Stack gap={2} mb="md">
        <Title order={4}>{title}</Title>
        <Text size="sm" c="dimmed">
          {sub}
        </Text>
      </Stack>
      {children}
    </Paper>
  );
}

const blankProvider = (type: AiProviderType = "openai"): AiProvider => ({
  id: "",
  name: PROVIDER_PRESETS[type].label,
  type,
  apiKey: "",
  baseUrl: PROVIDER_PRESETS[type].baseUrl,
  textModel: PROVIDER_PRESETS[type].textModel,
  imageModel: PROVIDER_PRESETS[type].imageModel,
});

function ProviderModal({
  initial,
  onClose,
  onSave,
}: {
  initial: AiProvider | null;
  onClose: () => void;
  onSave: (p: AiProvider) => void;
}) {
  const [p, setP] = useState<AiProvider>(initial ?? blankProvider());
  const set = (patch: Partial<AiProvider>) => setP((x) => ({ ...x, ...patch }));
  const valid = p.name.trim() && p.apiKey.trim() && p.baseUrl.trim() && p.textModel.trim();

  return (
    <Modal opened onClose={onClose} title={initial?.id ? "Edit provider" : "Add AI provider"} centered size="lg">
      <Stack>
        <Select
          label="Type"
          allowDeselect={false}
          data={(Object.keys(PROVIDER_PRESETS) as AiProviderType[]).map((t) => ({
            value: t,
            label: PROVIDER_PRESETS[t].label,
          }))}
          value={p.type}
          onChange={(v) => {
            const t = (v ?? "openai") as AiProviderType;
            const pre = PROVIDER_PRESETS[t];
            set({
              type: t,
              baseUrl: pre.baseUrl,
              textModel: pre.textModel,
              imageModel: pre.imageModel,
              name: p.name === PROVIDER_PRESETS[p.type].label ? pre.label : p.name,
            });
          }}
        />
        <TextInput label="Name" value={p.name} onChange={(e) => set({ name: e.currentTarget.value })} />
        <PasswordInput
          label="API key"
          value={p.apiKey}
          onChange={(e) => set({ apiKey: e.currentTarget.value })}
          description="Stored in your user profile, never in the repo. Passed to the engine via env."
        />
        <TextInput label="Base URL" value={p.baseUrl} onChange={(e) => set({ baseUrl: e.currentTarget.value })} />
        <SimpleGrid cols={2}>
          <TextInput
            label="Text model"
            description="Title + description"
            value={p.textModel}
            onChange={(e) => set({ textModel: e.currentTarget.value })}
          />
          <TextInput
            label="Image model"
            description="Blank = no AI thumbnail"
            value={p.imageModel}
            onChange={(e) => set({ imageModel: e.currentTarget.value })}
          />
        </SimpleGrid>
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!valid}
            onClick={() => onSave({ ...p, id: p.id || `p_${Date.now().toString(36)}` })}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export default function Settings() {
  const { config, setConfig, updateSettings, appInfo } = useStore();
  const s = config.settings;
  const [editing, setEditing] = useState<AiProvider | null | "new">(null);

  const pickBrand = async (kind: keyof BrandAssets) => {
    const picked = await openDialog({
      multiple: false,
      filters: [{ name: "Image", extensions: ["png", "jpg", "jpeg", "webp"] }],
    });
    if (!picked || Array.isArray(picked)) return;
    try {
      const dest = await api.copyBrandAsset(picked, kind);
      setConfig((c) => ({ ...c, brand: { ...c.brand, [kind]: dest } }));
      notifications.show({ color: "teal", message: `${kind} updated` });
    } catch (e) {
      notifications.show({ color: "red", title: "Copy failed", message: String(e) });
    }
  };

  const saveProvider = (p: AiProvider) => {
    setConfig((c) => {
      const exists = c.providers.some((x) => x.id === p.id);
      const providers = exists ? c.providers.map((x) => (x.id === p.id ? p : x)) : [...c.providers, p];
      return { ...c, providers, activeProviderId: c.activeProviderId ?? p.id };
    });
    setEditing(null);
  };

  const removeProvider = (id: string) => {
    setConfig((c) => ({
      ...c,
      providers: c.providers.filter((x) => x.id !== id),
      activeProviderId: c.activeProviderId === id ? null : c.activeProviderId,
    }));
  };

  return (
    <Container size="lg" py="lg">
      <Stack gap="lg">
        <Stack gap={4}>
          <Title order={2}>Settings</Title>
          <Text c="dimmed" size="sm">
            Saved automatically to your user profile.
          </Text>
        </Stack>

        <Section title="Brand assets" sub="Used for AI thumbnails. Exactly one logo is composited per thumb.">
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="lg">
            {BRAND.map(({ kind, label, hint }) => {
              const custom = config.brand[kind];
              const path = custom || appInfo?.brand_defaults[kind] || "";
              return (
                <Stack key={kind} gap="xs">
                  <img className="brand-tile" src={fileSrc(path, custom || "default")} alt={label} />
                  <Group justify="space-between" wrap="nowrap">
                    <Stack gap={0} style={{ minWidth: 0 }}>
                      <Group gap={6}>
                        <Text fw={600}>{label}</Text>
                        {!custom && (
                          <Badge size="xs" variant="light" color="gray">
                            default
                          </Badge>
                        )}
                      </Group>
                      <Text size="xs" c="dimmed">
                        {hint}
                      </Text>
                    </Stack>
                    <Group gap={4} wrap="nowrap">
                      <Tooltip label="Replace">
                        <ActionIcon variant="light" onClick={() => void pickBrand(kind)}>
                          <IconUpload size={16} />
                        </ActionIcon>
                      </Tooltip>
                      {custom && (
                        <Tooltip label="Reset to default">
                          <ActionIcon
                            variant="subtle"
                            color="gray"
                            onClick={() => setConfig((c) => ({ ...c, brand: { ...c.brand, [kind]: "" } }))}
                          >
                            <IconRestore size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </Group>
                  </Group>
                </Stack>
              );
            })}
          </SimpleGrid>
        </Section>

        <Section
          title="AI providers"
          sub="Add as many as you like. Pick which one runs per job from the Generate sidebar."
        >
          <Stack gap="sm">
            {config.providers.length === 0 && (
              <Text size="sm" c="dimmed">
                No providers. Titles, descriptions, and thumbnails use built-in fallbacks.
              </Text>
            )}
            <Radio.Group
              value={config.activeProviderId ?? ""}
              onChange={(v) => setConfig((c) => ({ ...c, activeProviderId: v || null }))}
            >
              <Stack gap="xs">
                {config.providers.map((p) => (
                  <Paper key={p.id} p="sm" radius="md" bg="var(--hl-panel-2)">
                    <Group justify="space-between" wrap="nowrap">
                      <Radio
                        value={p.id}
                        label={
                          <Stack gap={0}>
                            <Text fw={600} size="sm">
                              {p.name}
                            </Text>
                            <Text size="xs" c="dimmed">
                              {PROVIDER_PRESETS[p.type].label} · {p.textModel}
                              {p.imageModel ? ` · ${p.imageModel}` : " · no image model"} · key ••••
                              {p.apiKey.slice(-4)}
                            </Text>
                          </Stack>
                        }
                      />
                      <Group gap={4} wrap="nowrap">
                        <ActionIcon variant="subtle" onClick={() => setEditing(p)}>
                          <IconEdit size={16} />
                        </ActionIcon>
                        <ActionIcon variant="subtle" color="red" onClick={() => removeProvider(p.id)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Group>
                    </Group>
                  </Paper>
                ))}
              </Stack>
            </Radio.Group>
            <Button
              variant="light"
              leftSection={<IconPlus size={16} />}
              w="fit-content"
              onClick={() => setEditing("new")}
            >
              Add provider
            </Button>
          </Stack>
        </Section>

        <Section title="Player tracking" sub="Who counts as “me” in the kill feed.">
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput
              label="Username"
              value={s.username}
              onChange={(e) => updateSettings({ username: e.currentTarget.value })}
            />
            <TextInput
              label="Clan tags"
              description="Comma separated"
              value={s.clanTags}
              onChange={(e) => updateSettings({ clanTags: e.currentTarget.value })}
            />
          </SimpleGrid>
        </Section>

        <Section title="Paths" sub="Blank uses the app defaults.">
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput
              label="Work dir"
              placeholder={appInfo?.work_dir}
              value={s.workDir}
              onChange={(e) => updateSettings({ workDir: e.currentTarget.value })}
            />
            <TextInput
              label="Output dir"
              placeholder={appInfo?.output_dir}
              value={s.outputDir}
              onChange={(e) => updateSettings({ outputDir: e.currentTarget.value })}
            />
          </SimpleGrid>
        </Section>
      </Stack>

      {editing && (
        <ProviderModal
          initial={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSave={saveProvider}
        />
      )}
    </Container>
  );
}

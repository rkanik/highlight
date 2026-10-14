import { useState, type FormEvent } from "react";
import { Badge, Button, Group, Stack, Text, TextInput, Title } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { useStore } from "../state/store";
import mark from "../assets/hilvox-mark.png";

export default function Onboarding() {
  const { completeOnboarding, isDev } = useStore();
  const [username, setUsername] = useState("");
  const [clanTags, setClanTags] = useState("");
  const ready = username.trim().length > 0;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (ready) completeOnboarding(username, clanTags);
  };

  return (
    <div className="onboard">
      <form className="onboard-inner" onSubmit={submit}>
        <Stack gap={28}>
          <Group gap={18} align="center" wrap="nowrap" className="onboard-brand">
            <img src={mark} alt="" className="onboard-mark" />
            <Title className="onboard-wordmark">
              <span>HILVOX</span>
            </Title>
            {isDev && (
              <Badge size="md" radius="sm" variant="filled" color="red" className="dev-badge">
                DEV
              </Badge>
            )}
          </Group>

          <Stack gap={8} className="onboard-step">
            <Title order={2} className="onboard-headline">
              Who's on the trigger?
            </Title>
            <Text c="dimmed" maw={460}>
              Your in-game name tells Hilvox which knocks and kills in the feed are yours.
            </Text>
          </Stack>

          <Stack gap="md" className="onboard-step onboard-fields">
            <TextInput
              size="md"
              label="PUBG username"
              placeholder="exactly as it shows in the kill feed"
              value={username}
              onChange={(e) => setUsername(e.currentTarget.value)}
              autoFocus
              required
              spellCheck={false}
              autoComplete="off"
            />
            <TextInput
              size="md"
              label="Clan tag"
              description="Optional, comma separated"
              placeholder="ASA"
              value={clanTags}
              onChange={(e) => setClanTags(e.currentTarget.value)}
              spellCheck={false}
              autoComplete="off"
            />
          </Stack>

          <Group className="onboard-step">
            <Button
              type="submit"
              size="md"
              disabled={!ready}
              rightSection={<IconArrowRight size={18} className="onboard-arrow" />}
              className="onboard-cta"
            >
              Start making shorts
            </Button>
          </Group>
        </Stack>
      </form>
    </div>
  );
}

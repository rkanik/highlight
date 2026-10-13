import { AppShell, Badge, Group, NavLink, Progress, Stack, Text, Title } from "@mantine/core";
import {
  IconLayoutDashboard,
  IconMovie,
  IconPhotoVideo,
  IconSettings,
} from "@tabler/icons-react";
import { useStore, type Page } from "./state/store";
import Dashboard from "./pages/Dashboard";
import Generate from "./pages/Generate";
import Library from "./pages/Library";
import LibraryDetail from "./pages/LibraryDetail";
import Settings from "./pages/Settings";
import ConfigSidebar from "./components/ConfigSidebar";
import DropOverlay from "./components/DropOverlay";

const NAV: { page: Page; label: string; icon: typeof IconMovie }[] = [
  { page: "dashboard", label: "Dashboard", icon: IconLayoutDashboard },
  { page: "generate", label: "Generate", icon: IconMovie },
  { page: "library", label: "Library", icon: IconPhotoVideo },
  { page: "settings", label: "Settings", icon: IconSettings },
];

export default function App() {
  const { route, navigate, job, sources } = useStore();
  const onGenerate = route.page === "generate";

  return (
    <AppShell
      navbar={{ width: 232, breakpoint: 0 }}
      aside={{ width: 340, breakpoint: "sm", collapsed: { desktop: !onGenerate, mobile: !onGenerate } }}
      padding={0}
    >
      <AppShell.Navbar className="nav-rail" p="md">
        <Stack gap={2} mb="xl" mt={4} className="brand-block">
          <Title order={2} className="brand-mark">
            HIGHLIGHT
          </Title>
          <Text size="xs" c="dimmed" tt="uppercase" fw={600} lts={1.5}>
            VOD to shorts, offline
          </Text>
        </Stack>
        <Stack gap={4}>
          {NAV.map(({ page, label, icon: Icon }) => (
            <NavLink
              key={page}
              component="button"
              active={route.page === page}
              label={label}
              leftSection={<Icon size={18} stroke={1.6} />}
              onClick={() => navigate(page)}
              className="nav-link"
              rightSection={
                page === "generate" && sources.length ? (
                  <Badge size="xs" variant="light" circle>
                    {sources.length}
                  </Badge>
                ) : null
              }
            />
          ))}
        </Stack>
        {job.running && (
          <Stack gap={6} mt="auto" className="nav-job" onClick={() => navigate("generate")}>
            <Group justify="space-between">
              <Text size="xs" fw={600} tt="uppercase" c="ember.4">
                {job.mode === "scan" ? "Scanning" : "Assembling"}
              </Text>
              <Text size="xs" c="dimmed">
                {job.pct.toFixed(0)}%
              </Text>
            </Group>
            <Progress value={job.pct} size="sm" animated />
            <Text size="xs" c="dimmed" lineClamp={1}>
              {job.status}
            </Text>
          </Stack>
        )}
      </AppShell.Navbar>

      <AppShell.Main className="main-surface">
        <div key={`${route.page}:${route.detailDir ?? ""}`} className="page-enter">
          {route.page === "dashboard" && <Dashboard />}
          {route.page === "generate" && <Generate />}
          {route.page === "library" &&
            (route.detailDir ? <LibraryDetail dir={route.detailDir} /> : <Library />)}
          {route.page === "settings" && <Settings />}
        </div>
      </AppShell.Main>

      <AppShell.Aside className="config-aside">
        {onGenerate && <ConfigSidebar />}
      </AppShell.Aside>

      <DropOverlay />
    </AppShell>
  );
}

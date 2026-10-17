import * as React from "react"
import { BookMarkedIcon, DownloadIcon, ListFilterIcon, PanelLeftCloseIcon, PanelLeftOpenIcon, SettingsIcon } from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

// Width of the icon rail: icons only, or icons with their names. App adds the
// difference to the sidebar width so the list next to it keeps its own width.
export const RAIL_COLLAPSED = 49  // --sidebar-width-icon (3rem) + its border
export const RAIL_OPEN = 168

export type Section = "screen" | "download" | "zotero" | "settings"

const sections: { id: Section; title: string; icon: React.ComponentType }[] = [
  { id: "screen", title: "筛选", icon: ListFilterIcon },
  { id: "download", title: "下载", icon: DownloadIcon },
  { id: "zotero", title: "Zotero", icon: BookMarkedIcon },
]
const settings = { id: "settings" as const, title: "设置", icon: SettingsIcon }  // at the foot of the rail

function RailItem({ item, active, railOpen, onSection }: {
  item: { id: Section; title: string; icon: React.ComponentType }; active: boolean; railOpen: boolean; onSection: (s: Section) => void
}) {
  return (
    <SidebarMenuItem>
      <SidebarMenuButton tooltip={{ children: item.title, hidden: railOpen }} isActive={active} onClick={() => onSection(item.id)}
        className="h-9 gap-3 px-2.5">
        <item.icon />
        <span className={railOpen ? "truncate" : "sr-only"}>{item.title}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}

/** Icon rail on the left (sections) plus a second column whose content the section decides. */
export function AppSidebar({
  section,
  onSection,
  railOpen,
  onRailOpen,
  children,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  section: Section
  onSection: (s: Section) => void
  railOpen: boolean
  onRailOpen: (open: boolean) => void
  children?: React.ReactNode  // none: the section has no list column (downloads)
}) {
  const ToggleIcon = railOpen ? PanelLeftCloseIcon : PanelLeftOpenIcon
  return (
    <Sidebar collapsible="icon" className="overflow-hidden *:data-[sidebar=sidebar]:flex-row" {...props}>
      <Sidebar collapsible="none" className="shrink-0 border-r transition-[width] duration-200 ease-linear"
        style={{ width: railOpen ? RAIL_OPEN : RAIL_COLLAPSED }}>
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" className="h-8 p-0 hover:bg-transparent" tooltip={{ children: "LitLoop", hidden: railOpen }}>
                <img src="/litloop-icon.svg" alt="" className="size-8 shrink-0 rounded-lg" />
                <span className={railOpen ? "truncate text-base font-semibold" : "sr-only"}>LitLoop</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {sections.map((s) => <RailItem key={s.id} item={s} active={s.id === section} railOpen={railOpen} onSection={onSection} />)}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter className="border-t">
          <SidebarMenu>
            <RailItem item={settings} active={section === "settings"} railOpen={railOpen} onSection={onSection} />
            <SidebarMenuItem>
              <SidebarMenuButton tooltip={{ children: railOpen ? "收起侧栏" : "展开侧栏", hidden: railOpen }} onClick={() => onRailOpen(!railOpen)}
                className="h-9 gap-3 px-2.5 text-muted-foreground" aria-label={railOpen ? "收起侧栏" : "展开侧栏"}>
                <ToggleIcon />
                <span className={railOpen ? "truncate" : "sr-only"}>收起</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>

      {children && (
        <Sidebar collapsible="none" className="hidden min-w-0 flex-1 md:flex">
          {children}
        </Sidebar>
      )}
    </Sidebar>
  )
}

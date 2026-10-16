import * as React from "react"
import { BookMarkedIcon, DownloadIcon, ListFilterIcon, SettingsIcon } from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export type Section = "screen" | "download" | "zotero" | "settings"

const sections: { id: Section; title: string; icon: React.ComponentType }[] = [
  { id: "screen", title: "筛选", icon: ListFilterIcon },
  { id: "download", title: "下载", icon: DownloadIcon },
  { id: "zotero", title: "Zotero", icon: BookMarkedIcon },
  { id: "settings", title: "设置", icon: SettingsIcon },
]

/** Icon rail on the left (sections) plus a second column whose content the section decides. */
export function AppSidebar({
  section,
  onSection,
  children,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
  section: Section
  onSection: (s: Section) => void
  children: React.ReactNode
}) {
  return (
    <Sidebar collapsible="icon" className="overflow-hidden *:data-[sidebar=sidebar]:flex-row" {...props}>
      <Sidebar collapsible="none" className="w-[calc(var(--sidebar-width-icon)+1px)]! border-r">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" className="md:h-8 md:p-0" tooltip={{ children: "LitLoop", hidden: false }}>
                <img src="/litloop-icon.svg" alt="" className="size-8 rounded-lg" />
                <span className="truncate font-medium">LitLoop</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent className="px-1.5 md:px-0">
              <SidebarMenu>
                {sections.map((s) => (
                  <SidebarMenuItem key={s.id}>
                    <SidebarMenuButton
                      tooltip={{ children: s.title, hidden: false }}
                      isActive={s.id === section}
                      onClick={() => onSection(s.id)}
                      className="px-2.5 md:px-2"
                    >
                      <s.icon />
                      <span>{s.title}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>

      <Sidebar collapsible="none" className="hidden flex-1 md:flex">
        {children}
      </Sidebar>
    </Sidebar>
  )
}

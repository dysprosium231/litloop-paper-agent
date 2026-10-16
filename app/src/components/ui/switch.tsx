"use client"

import * as React from "react"
import { cn } from "cn"
import { Switch as SwitchPrimitive } from "radix-ui"

function Switch({
  className,
  size = "default",
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root> & {
  size?: "sm" | "default"
}) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      data-size={size}
      className={cn(
        "peer group/switch relative inline-flex shrink-0 items-center rounded-full border border-transparent transition-colors outline-none after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:ring-3 focus-visible:ring-ring/40 aria-invalid:border-destructive data-[size=default]:h-[22px] data-[size=default]:w-[40px] data-[size=sm]:h-[16px] data-[size=sm]:w-[28px] data-checked:bg-primary data-unchecked:bg-muted-foreground/35 hover:data-unchecked:bg-muted-foreground/45 data-disabled:cursor-not-allowed data-disabled:opacity-50",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none block rounded-full bg-white shadow-[0_1px_2px_rgba(0,0,0,0.25)] ring-0 transition-transform duration-150 group-data-[size=default]/switch:size-[18px] group-data-[size=sm]/switch:size-3 translate-x-[1px] group-data-[size=default]/switch:data-checked:translate-x-[19px] group-data-[size=sm]/switch:data-checked:translate-x-[13px]"
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }

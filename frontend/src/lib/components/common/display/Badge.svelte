<script lang="ts" module>
  import type { IconifyIcon } from 'iconify-icon';
  import type { Snippet } from 'svelte';
  import type { MouseEventHandler } from 'svelte/elements';

  export type BadgeProps = {
    /** The badge text snippet. */
    children: Snippet;
    /** The badge icon. */
    icon?: string | IconifyIcon;
    /** The badge icon color. */
    iconColor?: string | null;
    /** Whether to use uppercase text. */
    uppercase?: boolean;
    /** Whether to use a dashed border. */
    dashed?: boolean;
    /** Whether to add a shadow to the badge. */
    shadow?: boolean;
    /** The class names for the badge. */
    class?: string;
    iconClass?: string;
    /** The click event handler. */
    onclick?: MouseEventHandler<HTMLDivElement>;
  };
</script>

<script lang="ts">
  let {
    children,
    icon,
    iconColor,
    uppercase = false,
    dashed = false,
    shadow = true,
    class: _class,
    iconClass,
    onclick
  }: BadgeProps = $props();

  const badgeClass = 'badge h-fit flex-wrap gap-1 px-2 py-[1px] rounded-field text-xs font-medium opacity-70';
  let borderClass = $derived(`border-1 border-base-content/10 ${dashed ? 'border-dashed' : ''}`);
  let shadowClass = $derived(shadow ? 'shadow-inner' : '');
  let uppercaseClass = $derived(uppercase ? 'uppercase' : '');
  let btnClass = $derived(onclick ? 'btn-scale' : '');
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  tabindex={onclick ? 0 : undefined}
  role={onclick ? 'button' : 'generic'}
  {onclick}
  class="{badgeClass} {borderClass} {shadowClass} {uppercaseClass} {btnClass} {_class}"
>
  {#if icon}
    <iconify-icon {icon} width="1rem" class="size-4 {iconClass}" style:color={iconColor}></iconify-icon>
  {/if}
  {@render children()}
</div>

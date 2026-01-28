<script lang="ts" module>
  import type { IconifyIcon } from 'iconify-icon';
  import type { Snippet } from 'svelte';
  import type { MouseEventHandler } from 'svelte/elements';

  export type ButtonProps = Partial<{
    /** The button content snippet. */
    children: Snippet;
    /** The button icon. */
    icon: string | IconifyIcon;
    /** The button text. */
    text: string;
    /** The key of the preset size styles. */
    size: keyof typeof PRESETS;
    /** Whether to use a ghost button style. */
    ghost: boolean;
    /** Whether to make the button square. */
    square: boolean;
    /** Whether to add a border to the button. */
    border: boolean;
    /** Whether to add a shadow to the button. */
    shadow: boolean;
    /** Whether to show a loading spinner. */
    loading: boolean | null;
    /** Whether to disable the button. */
    disabled: boolean;
    /** The class names for the button. */
    class: string;
    /** The click event handler for the button. */
    onclick: MouseEventHandler<HTMLButtonElement>;
  }>;

  /**
   * The preset size styles for the button.
   */
  const PRESETS = {
    xs: {
      size: '1.5rem',
      iconSize: '1.125rem',
      textClass: 'text-xs'
    },
    sm: {
      size: '2rem',
      iconSize: '1.25rem',
      textClass: 'text-sm'
    },
    md: {
      size: '2.5rem',
      iconSize: '1.75rem',
      textClass: 'text-base'
    }
  };
</script>

<script lang="ts">
  import { tooltip } from '$lib/actions';
  import { hideAll } from 'tippy.js';

  let {
    children,
    icon,
    text,
    size = 'sm',
    ghost = true,
    square = true,
    border = false,
    shadow = false,
    loading = false,
    disabled = false,
    class: _class,
    onclick
  }: ButtonProps = $props();

  const preset = $derived(PRESETS[size]);

  let ghostClass = $derived(ghost ? 'btn-subtle' : '');
  let squareClass = $derived(square ? 'p-0' : 'px-2');
  let borderClass = $derived(border ? 'border' : '');
  let shadowClass = $derived(shadow && !disabled ? 'shadow-sm' : '');
</script>

<button
  type="button"
  class="btn truncate opacity-80 {ghostClass} {squareClass} {borderClass} {shadowClass} {_class}"
  style:width={square ? preset.size : ''}
  style:height={preset.size}
  style:min-height={preset.size}
  {disabled}
  onclick={(event) => {
    hideAll(); // hide all tooltips
    !disabled && !loading && onclick?.(event);
  }}
  use:tooltip={{
    content: square ? text : '',
    followCursor: true
  }}
>
  {#if children}
    {@render children()}
  {:else}
    {#if loading}
      <span class="loading loading-spinner" style:width={preset.iconSize}></span>
    {:else}
      <iconify-icon {icon} width={preset.iconSize}></iconify-icon>
    {/if}
    <span class="truncate {square ? 'sr-only' : preset.textClass}" title={text}>{text}</span>
  {/if}
</button>

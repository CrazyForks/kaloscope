<script lang="ts" module>
  type UploaderProps = Partial<{
    up: string | null;
    at: string | null;
    extra: string | null;
    class: string;
  }>;
</script>

<script lang="ts">
  let { up, at, extra, class: _class }: UploaderProps = $props();
</script>

<div class="flex items-center justify-between gap-4 pr-1 text-xs opacity-50 {_class}">
  {#if up || at}
    <span>{up ? `UP: ${up}` : ''}{up && at ? ' ・ ' : ''}{at}</span>
  {/if}
  {#if extra}
    <span class="italic">{extra}</span>
  {/if}
</div>

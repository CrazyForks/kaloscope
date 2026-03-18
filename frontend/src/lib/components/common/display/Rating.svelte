<script lang="ts" module>
  export type RatingProps = {
    /** The rating score to display. */
    score?: number | null;
    /** The number of decimal places to show. */
    decimals?: number;
    /** Whether to apply a stroke effect to the text. */
    stroke?: boolean;
    /** The class names for the rating badge. */
    class?: string;
  };
</script>

<script lang="ts">
  let { score, decimals = 1, stroke = true, class: _class }: RatingProps = $props();
</script>

{#if score !== null && score !== undefined}
  <span
    class:text-stroke={stroke}
    class="flex-center rounded-selector bg-black/60 px-2 text-yellow-500 opacity-80 {_class}"
  >
    {Number(score).toFixed(decimals)}
  </span>
{/if}

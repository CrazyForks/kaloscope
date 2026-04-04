<script lang="ts" module>
  export type RatingProps = {
    /** The rating score to display. */
    score?: number | null;
    /** The minimum value of the rating. */
    min?: number;
    /** The maximum value of the rating. */
    max?: number;
    /** The number of decimal places to show. */
    decimals?: number;
    /** Whether to apply a stroke effect to the text. */
    stroke?: boolean;
    /** The class names for the rating badge. */
    class?: string;
  };
</script>

<script lang="ts">
  let { score: _score, min = 0, max = 10, decimals = 1, stroke = true, class: _class }: RatingProps = $props();

  let score: string = $derived.by(() => {
    if (_score === null || _score === undefined) {
      return '';
    }
    if (_score <= min) {
      return min ? min.toString() : '';
    }
    if (_score >= max) {
      return max.toString();
    }
    return Number(_score).toFixed(decimals);
  });
</script>

{#if score}
  <span
    class:text-stroke={stroke}
    class="flex-center rounded-selector bg-black/60 px-2 text-yellow-500 opacity-80 {_class}"
  >
    {score}
  </span>
{/if}

import { Plugin } from 'xgplayer';
import './index.css';

/**
 * Decorative gradients that keep player controls legible over video.
 */
export default class Gradient extends Plugin {
  /**
   * The xgplayer plugin name.
   */
  static get pluginName() {
    return 'gradient';
  }

  /**
   * Render the top and bottom gradient layers.
   */
  render() {
    return `
      <xg-gradient class="xgplayer-gradient top"></xg-gradient>
      <xg-gradient class="xgplayer-gradient bottom"></xg-gradient>
    `;
  }
}

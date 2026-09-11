const SPEEDS = [1, 2, 5, 10];

type Props = {
  playing: boolean;
  speed: number;
  cursor: number;
  total: number;
  onPlay: () => void;
  onPause: () => void;
  onSpeed: (speed: number) => void;
  onReset: () => void;
};

export default function PlaybackBar(props: Props) {
  return (
    <div className="playback">
      {props.playing ? (
        <button onClick={props.onPause}>Pause</button>
      ) : (
        <button className="play" onClick={props.onPlay} disabled={props.cursor >= props.total && props.total > 0}>
          Play
        </button>
      )}
      <button onClick={props.onReset}>Reset day</button>
      {SPEEDS.map((item) => (
        <button
          key={item}
          className={props.speed === item ? "active" : ""}
          onClick={() => props.onSpeed(item)}
        >
          {item}×
        </button>
      ))}
      <div className="progress">
        {props.cursor} / {props.total} bars
      </div>
    </div>
  );
}

import { ArrowRight, Plane, X } from "lucide-react";
import { LiveryItem } from "../types";
import { hubLabel } from "../hubFlag";
import { displayLiveryName } from "../liveryName";

interface AircraftListModalProps {
  item: LiveryItem;
  onClose: () => void;
  onViewInFleet: (liveryName: string) => void;
}

export function AircraftListModal({ item, onClose, onViewInFleet }: AircraftListModalProps) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="livery-modal" role="dialog" aria-label={`Aircraft flying ${item.name}`} onClick={(event) => event.stopPropagation()}>
        <div className="livery-modal-header">
          <div>
            <p className="section-kicker">{item.aircraft.length} aircraft assigned</p>
            <h3>{displayLiveryName(item)}</h3>
          </div>
          <button onClick={onClose} aria-label="Close aircraft list"><X size={16} /></button>
        </div>

        <div className="livery-modal-list">
          {item.aircraft.map((plane) => (
            <div className="livery-modal-row" key={plane.aircraft_id}>
              <div className="livery-modal-plane">
                <span className="livery-modal-plane-icon"><Plane size={14} /></span>
                <div>
                  <strong>{plane.name}</strong>
                  <small>{plane.model}{plane.hub ? ` · Hub ${hubLabel(plane.hub, plane.country_code)}` : ""}</small>
                </div>
              </div>
              <span className={`livery-modal-util${plane.utilization >= 100 ? " is-full" : plane.utilization > 0 ? " is-partial" : ""}`}>
                {plane.utilization.toFixed(0)}% util
              </span>
            </div>
          ))}
        </div>

        <div className="livery-modal-footer">
          <button className="ghost-button" onClick={onClose}>Close</button>
          <button className="livery-view-button" onClick={() => { onClose(); onViewInFleet(item.name); }}>
            <span>View in Fleet</span>
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

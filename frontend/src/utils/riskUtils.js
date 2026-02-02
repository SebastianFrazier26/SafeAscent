/**
 * Risk Interpretation Utilities
 *
 * Functions for interpreting and displaying risk scores and confidence levels.
 */

/**
 * Get risk level from risk score
 *
 * @param {number} riskScore - Risk score (0-100)
 * @returns {string} Risk level: 'low', 'moderate', 'high', or 'extreme'
 */
export const getRiskLevel = (riskScore) => {
  if (riskScore < 25) return 'low';
  if (riskScore < 50) return 'moderate';
  if (riskScore < 75) return 'high';
  return 'extreme';
};

/**
 * Get risk color for visualization
 *
 * @param {number} riskScore - Risk score (0-100)
 * @returns {string} Tailwind color class
 */
export const getRiskColor = (riskScore) => {
  const level = getRiskLevel(riskScore);
  const colors = {
    low: 'bg-risk-low',
    moderate: 'bg-risk-moderate',
    high: 'bg-risk-high',
    extreme: 'bg-risk-extreme',
  };
  return colors[level];
};

/**
 * Get risk description
 *
 * @param {number} riskScore - Risk score (0-100)
 * @returns {string} Human-readable risk description
 */
export const getRiskDescription = (riskScore) => {
  const level = getRiskLevel(riskScore);
  const descriptions = {
    low: 'Low Risk - Favorable conditions based on historical data',
    moderate: 'Moderate Risk - Exercise caution and prepare accordingly',
    high: 'High Risk - Significant hazards present, reconsider route',
    extreme: 'Extreme Risk - Dangerous conditions, strongly advise against',
  };
  return descriptions[level];
};

/**
 * Get confidence interpretation
 *
 * @param {number} confidence - Confidence score (0-100)
 * @returns {Object} Confidence level and description
 */
export const getConfidenceInfo = (confidence) => {
  if (confidence >= 75) {
    return {
      level: 'High',
      description: 'Prediction based on substantial accident data in this region',
      color: 'text-green-600',
    };
  } else if (confidence >= 50) {
    return {
      level: 'Medium',
      description: 'Moderate amount of accident data available for this area',
      color: 'text-yellow-600',
    };
  } else if (confidence >= 25) {
    return {
      level: 'Low',
      description: 'Limited accident data in this region - use caution',
      color: 'text-orange-600',
    };
  } else {
    return {
      level: 'Very Low',
      description: 'Very limited data - prediction may be unreliable',
      color: 'text-red-600',
    };
  }
};

/**
 * Format risk score for display
 *
 * @param {number} riskScore - Risk score (0-100)
 * @returns {string} Formatted risk score
 */
export const formatRiskScore = (riskScore) => {
  return `${Math.round(riskScore)}/100`;
};

/**
 * Format confidence for display
 *
 * @param {number} confidence - Confidence score (0-100)
 * @returns {string} Formatted confidence
 */
export const formatConfidence = (confidence) => {
  return `${Math.round(confidence)}%`;
};

/**
 * Get map marker color based on risk score
 *
 * @param {number} riskScore - Risk score (0-100)
 * @returns {string} Hex color code for map marker
 */
export const getMarkerColor = (riskScore) => {
  const level = getRiskLevel(riskScore);
  const colors = {
    low: '#10b981',      // green
    moderate: '#f59e0b', // yellow
    high: '#ef4444',     // red
    extreme: '#7c2d12',  // dark red
  };
  return colors[level];
};

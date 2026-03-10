"""
Firebase Firestore client wrapper with resilience patterns.
Serves as the central nervous system for state management.
"""
import json
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

import firebase_admin
from firebase_admin import firestore, credentials
from google.cloud.firestore_v1.base_client import BaseClient
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)


class FirebaseClient:
    """Thread-safe Firebase Firestore client with retry logic."""
    
    def __init__(self, service_account_path: str, project_id: str):
        self.service_account_path = service_account_path
        self.project_id = project_id
        self._client: Optional[BaseClient] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        
    async def initialize(self) -> None:
        """Initialize Firebase connection with retry logic."""
        if self._initialized:
            return
            
        async with self._lock:
            try:
                if not firebase_admin._apps:
                    cred = credentials.Certificate(self.service_account_path)
                    firebase_admin.initialize_app(cred, {
                        'projectId': self.project_id
                    })
                
                self._client = firestore.client()
                self._initialized = True
                logger.info(f"Firebase client initialized for project: {self.project_id}")
                
                # Test connection
                await self._test_connection()
                
            except Exception as e:
                logger.error(f"Failed to initialize Firebase client: {e}")
                raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((Exception,)),
        reraise=True
    )
    async def _test_connection(self) -> None:
        """Test Firebase connection by reading system state."""
        if not self._client:
            raise RuntimeError("Firebase client not initialized")
        
        doc_ref = self._client.collection("system").document("state")
        doc = doc_ref.get()
        
        if doc.exists:
            logger.debug("Firebase connection test successful")
        else:
            logger.info("Firebase connection successful, no system state found (first run)")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        reraise=True
    )
    async def update_system_state(self, updates: Dict[str, Any]) -> None:
        """Update system state document atomically."""
        if not self._client:
            raise RuntimeError("Firebase client not initialized")
        
        try:
            doc_ref = self._client.collection("system").document("state")
            
            # Add timestamp
            updates["last_updated"] = firestore.SERVER_TIMESTAMP
            updates["updated_at"] = datetime.utcnow().isoformat()
            
            # Use transaction for atomic update
            @firestore.transactional
            def update_in_transaction(transaction, doc_ref, updates):
                snapshot = doc_ref.get(transaction=transaction)
                
                if snapshot.exists:
                    transaction.update(doc_ref, updates)
                else:
                    transaction.set(doc_ref, updates)
            
            transaction = self._client.transaction()
            update_in_transaction(transaction, doc_ref, updates)
            
            logger.debug(f"System state updated: {list(updates.keys())}")
            
        except Exception as e:
            logger.error(f"Failed to update system state: {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        reraise=True
    )
    async def log_opportunity(self, opportunity: Dict[str, Any]) -> str:
        """Log a discovered arbitrage opportunity with idempotency check."""
        if not self._client:
            raise RuntimeError("Firebase client not initialized")
        
        try:
            # Generate opportunity ID from hash of critical fields
            import hashlib
            opp_hash = hashlib.md5(
                json.dumps({
                    'path': opportunity.get('path'),
                    'timestamp': opportunity.get('timestamp')
                }, sort_keys=True).encode()
            ).hexdigest()
            
            # Check for duplicates
            existing = self._client.collection("opportunities").document(opp_hash).get()
            if existing.exists:
                logger.debug(f"Duplicate opportunity skipped: {opp_hash}")
                return opp_hash
            
            # Add metadata
            opportunity["id"] = opp_hash
            opportunity["created_at"] = firestore.SERVER_TIMESTAMP
            opportunity["instance_id"] = opportunity.get("instance_id", "unknown")
            opportunity["status"] = "discovered"
            
            # Store opportunity
            doc_ref = self._client.collection("opportunities").document(opp_hash)
            doc_ref.set(opportunity)
            
            logger.info(f"Logged opportunity {opp_hash}: {opportunity.get('path', 'unknown')}")
            return opp_hash
            
        except Exception as e:
            logger.error(f"Failed to log opportunity: {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=5)
    )
    async def update_opportunity_status(self, opportunity_id: str, status: str, 
                                       tx_hash: Optional[str] = None, 
                                       error: Optional[str] = None) -> None:
        """Update opportunity execution status."""
        if not self._client:
            raise RuntimeError("Firebase client not initialized")
        
        updates = {
            "status": status,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        
        if tx_hash:
            updates["tx_hash"] = tx_hash
        if error:
            updates["error"] = error
        
        try:
            doc_ref = self._client.collection("opportunities").document(opportunity_id)
            doc_ref.update(updates)
            logger.debug(f"